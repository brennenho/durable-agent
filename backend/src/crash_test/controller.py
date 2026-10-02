"""One scoped fault per scenario; observations are independent of application storage."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from durable_agent.crash_test import HookEvent, HookReply

from crash_test.docker import DockerControl, DockerError


def now() -> str:
    return datetime.now(UTC).isoformat()


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.events: list[dict] = []
        self.lock = asyncio.Lock()

    async def record(self, kind: str, label: str, **data):
        event = {"id": str(uuid4()), "at": now(), "kind": kind, "label": label, **data}
        async with self.lock:
            if len(self.events) >= 2000:
                raise RuntimeError("Scenario observation limit exceeded")

            def append():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as output:
                    output.write(json.dumps(event) + "\n")
                    output.flush()

            await asyncio.to_thread(append)
            self.events.append(event)
        return event


class FaultController:
    def __init__(
        self,
        docker: DockerControl,
        journal: Journal,
        run_id: UUID,
        hook: str | None,
        *,
        container_lock: asyncio.Lock | None = None,
    ):
        self.docker, self.journal, self.run_id, self.hook = docker, journal, run_id, hook
        self.operations: dict[UUID, int] = {}
        self.seen: set[UUID] = set()
        self.trigger: HookEvent | None = None
        self.killed = False
        self.error: str | None = None
        self.task: asyncio.Task | None = None
        self.closed = False
        self.container_lock = container_lock or asyncio.Lock()

    async def observe(self, event: HookEvent) -> HookReply:
        if self.closed or event.event_id in self.seen:
            return HookReply(action="continue")
        age = (datetime.now(UTC) - event.observed_at).total_seconds()
        if not 0 <= age < 8 or event.operation_id is None:
            return HookReply(action="continue")
        if event.run_id == self.run_id and event.name == "worker.after_claim":
            self.operations[event.operation_id] = max(
                self.operations.get(event.operation_id, 0), event.lease_generation or 0
            )
        if (event.run_id is not None and event.run_id != self.run_id) or (
            event.run_id is None and event.operation_id not in self.operations
        ):
            return HookReply(action="continue")
        if len(self.seen) >= 2000:
            raise RuntimeError("Scenario hook limit exceeded")
        self.seen.add(event.event_id)
        await self.journal.record(
            "hook",
            event.name,
            operation_id=str(event.operation_id),
            hook=event.model_dump(mode="json"),
        )
        if (
            self.closed
            or event.name != self.hook
            or self.trigger is not None
            or event.lease_generation != self.operations.get(event.operation_id)
        ):
            return HookReply(action="continue")
        # Consume before the first await: duplicate/concurrent hooks cannot trigger twice.
        self.trigger = event
        self.task = asyncio.create_task(self._kill_and_restore(event))
        await asyncio.shield(self.task)
        return HookReply(action="continue")

    async def _kill_and_restore(self, event: HookEvent):
        remaining = max(0, 8 - (datetime.now(UTC) - event.observed_at).total_seconds())
        try:
            async with asyncio.timeout(remaining):
                await self.container_lock.acquire()
        except TimeoutError:
            self.error = "Hook boundary expired while waiting for a container action"
            await self.journal.record("fault_error", "Fault injection failed", detail=self.error)
            return
        try:
            await self._apply_fault(event)
        finally:
            self.container_lock.release()

    async def _apply_fault(self, event: HookEvent):
        target = None
        attempted = False
        try:
            if event.sandbox_id:
                target = await self.docker.inspect(event.sandbox_id)
                if target.service != "sandbox":
                    raise DockerError("Hook target is not a sandbox")
            else:
                matches = [
                    item
                    for item in await self.docker.inventory()
                    if item.service == event.service and item.instance == event.instance
                ]
                if len(matches) != 1:
                    raise DockerError("Hook emitter could not be resolved uniquely")
                target = matches[0]
            if datetime.fromisoformat(target.started_at.replace("Z", "+00:00")) > event.observed_at:
                raise DockerError("Hook came from a previous process incarnation")
            if not target.running or (datetime.now(UTC) - event.observed_at).total_seconds() >= 8:
                raise DockerError("Hook boundary or running target is no longer current")
            await self.journal.record(
                "kill_requested",
                f"Kill requested: {target.service}",
                target=target.json(),
                operation_id=str(event.operation_id),
            )
            if self.closed or event.lease_generation != self.operations.get(event.operation_id):
                raise DockerError("Hook was disarmed or superseded before injection")
            remaining = 8 - (datetime.now(UTC) - event.observed_at).total_seconds()
            if remaining <= 0:
                raise DockerError("Hook boundary expired before injection")
            attempted = True
            # Bound selection, Docker's reinspection and the stop to the held boundary.
            async with asyncio.timeout(remaining):
                stopped = await self.docker.kill(target)
            self.killed = True
            await self.journal.record(
                "kill_observed",
                f"Process stopped: {target.service}",
                target=stopped.json(),
                operation_id=str(event.operation_id),
            )
            if (datetime.now(UTC) - event.observed_at).total_seconds() >= 10:
                raise DockerError("Kill verified after the hook's guaranteed hold window")
        except (TimeoutError, DockerError, OSError, ValueError) as exc:
            self.error = str(exc) or "Fault injection exceeded the hook window"
            await self.journal.record("fault_error", "Fault injection failed", detail=self.error)
        finally:
            if attempted and target and target.service != "sandbox":
                try:
                    current = await self.docker.inspect(target.id)
                    if not current.running and current.started_at == target.started_at:
                        started = await self.docker.start(current)
                        await self.journal.record(
                            "restart",
                            f"Container started: {target.service}",
                            target=started.json(),
                            operation_id=str(event.operation_id),
                        )
                except (DockerError, OSError) as exc:
                    self.error = f"Recovery failed: {exc}"
                    await self.journal.record("fault_error", self.error)

    async def close(self):
        self.closed = True
        if self.task:
            await asyncio.shield(self.task)
