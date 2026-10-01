"""Execute once, then redeliver the same outcome while ownership remains valid."""

import asyncio
import logging
from uuid import UUID

from durable_agent.contracts import (
    Assignment,
    AttemptOutcome,
    ExecutionError,
    FailedOutcome,
    ReportRequest,
)
from durable_agent.worker.lease import Lease, LeaseLost
from durable_agent.worker.openai_client import OpenAIClient
from durable_agent.worker.services import ServiceClient, ServiceError

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        services: ServiceClient,
        models: OpenAIClient,
        worker_id: UUID,
        *,
        heartbeat_interval: float = 5,
        report_retry_interval: float = 1,
    ):
        self.services = services
        self.models = models
        self.worker_id = worker_id
        self.heartbeat_interval = heartbeat_interval
        self.report_retry_interval = report_retry_interval

    async def run_assignment(self, assignment: Assignment, *, claimed_at: float) -> None:
        lease = Lease(
            self.services,
            assignment,
            self.worker_id,
            claimed_at=claimed_at,
            heartbeat_interval=self.heartbeat_interval,
        )
        await lease.guard(lambda: self._execute_and_report(lease))

    async def _execute(self, lease: Lease) -> AttemptOutcome:
        assignment = lease.assignment
        try:
            remaining = lease.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout_at(lease.deadline):
                if assignment.operation.kind == "model":
                    return await self.models.execute(
                        assignment.config, assignment.operation.request, timeout_seconds=remaining
                    )
                return await self.services.execute_tool(assignment, timeout_seconds=remaining)
        except TimeoutError:
            error = ExecutionError(
                code="call_timeout", message="Call deadline exceeded", retryable=True
            )
        except ServiceError as exc:
            if exc.status_code == 409:
                raise LeaseLost("Tool service rejected the attempt") from exc
            error = exc.error
        except ValueError:
            error = ExecutionError(
                code="invalid_operation_result",
                message="Service returned an invalid result",
                retryable=False,
            )
        return FailedOutcome(status="failed", error=error)

    async def _execute_and_report(self, lease: Lease) -> None:
        outcome = await self._execute(lease)
        report = ReportRequest(
            worker_id=self.worker_id,
            lease_generation=lease.assignment.lease_generation,
            outcome=outcome,
        )
        # Freeze the delivery budget at completion; renewals cannot extend it forever.
        report_until = lease.expires
        if asyncio.get_running_loop().time() >= report_until:
            raise LeaseLost("Lease expired before reporting")
        try:
            async with asyncio.timeout_at(report_until):
                while True:
                    delay = self.report_retry_interval
                    try:
                        await self.services.report(lease.assignment, report)
                        return
                    except ServiceError as exc:
                        if exc.status_code == 409:
                            raise LeaseLost("Control rejected the report") from exc
                        if not exc.error.retryable:
                            raise
                        delay = max(delay, exc.error.retry_after_seconds or 0)
                        logger.warning(
                            "Report delivery failed for %s: %s", lease.assignment.attempt_id, exc
                        )
                    except ValueError:
                        logger.warning(
                            "Invalid report acknowledgment for %s", lease.assignment.attempt_id
                        )
                    await asyncio.sleep(delay)
        except TimeoutError as exc:
            raise LeaseLost("Report delivery budget expired") from exc
