"""Serialize operator actions while allowing hook receipts and observation during outages."""

import asyncio
from pathlib import Path

import httpx
from durable_agent.crash_test import HookReply
from fastapi import HTTPException

from crash_test.contracts import ContainerAction
from crash_test.controller import Journal, now
from crash_test.docker import DockerControl, DockerError
from crash_test.runner import ScenarioRun
from crash_test.scenarios import SCENARIOS


class ControlRoom:
    def __init__(self, docker: DockerControl, client: httpx.AsyncClient, root: Path):
        self.docker, self.client, self.root = docker, client, root
        self.current: ScenarioRun | None = None
        self.task: asyncio.Task | None = None
        self.mutation = asyncio.Lock()
        self.inventory_lock = asyncio.Lock()
        self.services, self.services_at, self.services_error = [], None, None
        self.journal = Journal(root / "controls.jsonl")
        self.autoscaling_enabled = True
        self.autoscaling_checked_at: str | None = None

    async def set_autoscaling(self, enabled: bool):
        async with self.mutation:
            await self.journal.record(
                "autoscaling", "Autoscaling allowed" if enabled else "Autoscaling paused"
            )
            self.autoscaling_enabled = enabled
        return {"enabled": enabled}

    def autoscaling_hook(self) -> HookReply:
        self.autoscaling_checked_at = now()
        return HookReply(action="continue" if self.autoscaling_enabled else "interrupt")

    def require_idle(self):
        if (self.task and not self.task.done()) or self.mutation.locked():
            raise HTTPException(409, "A scenario or container action is already running")

    async def start(self, name: str, run_id=None):
        self.require_idle()
        if name not in SCENARIOS:
            raise HTTPException(422, "Unknown scenario")
        self.current = ScenarioRun(name, self.docker, self.client, self.root, run_id=run_id)
        current = self.current
        self.task = asyncio.create_task(current.execute())
        self.task.add_done_callback(lambda task: self._finished(task, current))
        ready = asyncio.create_task(current.ready.wait())
        try:
            # Do not acknowledge arming while preflight can still miss the first claim.
            await asyncio.wait({ready, self.task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
        return current.snapshot()

    def _finished(self, task, current):
        if not task.cancelled() and (error := task.exception()):
            current.status, current.error = "inconclusive", f"Runner error: {error}"

    async def action(self, body: ContainerAction):
        self.require_idle()
        async with self.mutation:
            target = await self.docker.inspect(body.id)
            if target.started_at != body.started_at:
                raise HTTPException(409, "Container restarted since it was selected")
            await self.journal.record(
                "action_requested",
                f"{body.action.title()} requested: {target.service}",
                target=target.json(),
            )
            try:
                result = await getattr(self.docker, body.action)(target)
            except (DockerError, OSError) as exc:
                await self.journal.record("fault_error", "Container action failed", detail=str(exc))
                raise
            label = "Process stopped" if body.action == "kill" else "Container started"
            await self.journal.record(
                "kill_observed" if body.action == "kill" else "restart",
                f"{label}: {target.service}",
                target=result.json(),
            )
            return result

    async def snapshot(self):
        async with self.inventory_lock:
            try:
                self.services = await self.docker.inventory()
                self.services_at, self.services_error = now(), None
            except (DockerError, OSError) as exc:
                self.services_error = str(exc)
        return {
            "project": self.docker.project,
            "services": self.services,
            "services_at": self.services_at,
            "services_error": self.services_error,
            "current": self.current.snapshot() if self.current else None,
            "scenarios": [
                {"name": name, "hook": hook, "description": description}
                for name, (hook, description) in SCENARIOS.items()
            ],
            "controls": self.journal.events,
            "autoscaling_enabled": self.autoscaling_enabled,
            "autoscaling_checked_at": self.autoscaling_checked_at,
        }

    async def close(self):
        async with self.mutation:
            current, task = self.current, self.task
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if current.status == "running":
                    current.status, current.error = "inconclusive", "Runner stopped"
