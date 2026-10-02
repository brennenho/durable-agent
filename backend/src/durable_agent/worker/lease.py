"""A lease bounds ownership; heartbeats never extend the call deadline."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from durable_agent.contracts import Assignment
from durable_agent.worker.services import ServiceClient, ServiceError

logger = logging.getLogger(__name__)


class LeaseLost(Exception):
    pass


class Lease:
    def __init__(
        self,
        services: ServiceClient,
        assignment: Assignment,
        worker_id: UUID,
        *,
        claimed_at: float,
        heartbeat_interval: float = 5,
    ):
        self.services = services
        self.assignment = assignment
        self.worker_id = worker_id
        self.heartbeat_interval = heartbeat_interval
        # Control sets the operation budget; anchor it before the claim round trip.
        self.deadline = (
            claimed_at + (assignment.deadline_at - assignment.server_time).total_seconds()
        )
        self.expires = (
            claimed_at + (assignment.lease_expires_at - assignment.server_time).total_seconds()
        )

    async def guard[T](self, work: Callable[[], Coroutine[Any, Any, T]]) -> T:
        if asyncio.get_running_loop().time() >= self.expires:
            raise LeaseLost("Claim arrived after its lease expired")
        execution = asyncio.create_task(work())
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            done, _ = await asyncio.wait(
                (execution, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if execution in done:
                return await execution
            await heartbeat  # Raises the reason ownership was lost.
            raise LeaseLost("Heartbeat stopped")
        finally:
            execution.cancel()
            heartbeat.cancel()
            await asyncio.gather(execution, heartbeat, return_exceptions=True)

    async def _heartbeat(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                async with asyncio.timeout_at(self.expires):
                    await asyncio.sleep(self.heartbeat_interval)
                    sent_at = loop.time()
                    renewed = await self.services.heartbeat(self.assignment, self.worker_id)
            except TimeoutError as exc:
                raise LeaseLost("Lease expired before renewal") from exc
            except ValueError as exc:
                raise LeaseLost("Control returned an invalid heartbeat") from exc
            except ServiceError as exc:
                if exc.status_code == 409 or not exc.error.retryable:
                    raise LeaseLost("Heartbeat rejected") from exc
                logger.warning("Heartbeat failed for %s: %s", self.assignment.attempt_id, exc)
                continue
            if renewed.deadline_at != self.assignment.deadline_at:
                raise LeaseLost("Control changed the fixed call deadline")
            self.expires = (
                sent_at + (renewed.lease_expires_at - renewed.server_time).total_seconds()
            )
            if loop.time() >= self.expires:
                raise LeaseLost("Renewal arrived after lease expiry")
