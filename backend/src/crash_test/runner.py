"""Arm one fault for an existing or next run; never replace the application's workers."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from durable_agent.contracts import RunDetail, ToolCheckpoint
from durable_agent.crash_test import HookEvent, HookReply

from crash_test.controller import FaultController, Journal, now
from crash_test.docker import DockerControl, DockerError
from crash_test.scenarios import SCENARIOS, evaluate


class ScenarioRun:
    def __init__(
        self,
        scenario: str,
        docker: DockerControl,
        client: httpx.AsyncClient,
        root: Path,
        *,
        run_id: UUID | None = None,
        container_lock: asyncio.Lock | None = None,
    ):
        self.hook, _ = SCENARIOS[scenario]
        self.id, self.scenario = str(uuid4()), scenario
        self.docker, self.client = docker, client
        self.journal = Journal(root / f"{self.id}.jsonl")
        self.container_lock = container_lock or asyncio.Lock()
        self.controller: FaultController | None = None
        self.run_id = run_id
        self.armed = False
        self.armed_at: datetime | None = None
        self.ready = asyncio.Event()
        self.selected = asyncio.Event()
        self.run: RunDetail | None = None
        self.child_runs: dict[UUID, RunDetail] = {}
        self.checkpoints: dict[str, dict] = {}
        self.observed_at: str | None = None
        self.error: str | None = None
        self.status = "running"
        self.checks: dict[str, bool] = {}

    def snapshot(self):
        return {
            "id": self.id,
            "scenario": self.scenario,
            "status": self.status,
            "run": self.run.model_dump(mode="json") if self.run else None,
            "child_runs": [run.model_dump(mode="json") for run in self.child_runs.values()],
            "observed_at": self.observed_at,
            "error": self.error,
            "checkpoints": self.checkpoints.copy(),
            "checks": self.checks.copy(),
            "events": self.journal.events.copy(),
            "manual_intervention": any(e["kind"] == "manual_action" for e in self.journal.events),
        }

    async def observe_hook(self, event: HookEvent) -> HookReply:
        if not self.armed:
            return HookReply(action="continue")
        if self.controller is None:
            if (
                event.name != "worker.after_claim"
                or event.service != "worker"
                or not event.run_id
                or not event.operation_id
                or not event.attempt_id
                or not event.lease_generation
                or event.lease_generation < 1
                or event.observed_at < self.armed_at
                or not 0 <= (datetime.now(UTC) - event.observed_at).total_seconds() < 8
            ):
                return HookReply(action="continue")
            workers = await self.docker.inventory()
            if not any(
                c.service == "worker"
                and c.instance == event.instance
                and c.running
                and datetime.fromisoformat(c.started_at.replace("Z", "+00:00")) <= event.observed_at
                for c in workers
            ):
                return HookReply(action="continue")
            # Inventory yields; another claim or cancellation may have won the selection.
            if not self.armed or self.controller is not None:
                return HookReply(action="continue")
            self.run_id = event.run_id
            self.controller = FaultController(
                self.docker,
                self.journal,
                self.run_id,
                self.hook,
                container_lock=self.container_lock,
            )
            self.selected.set()
        if self.controller:
            # A child's first claim can arrive before the next poll discovers it.
            if event.run_id and event.run_id not in self.controller.run_ids:
                try:
                    self._register_children(await self._read_run(self.run_id))
                except httpx.HTTPError as exc:
                    self.error = str(exc)
                    return HookReply(action="continue")
            return await self.controller.observe(event)
        return HookReply(action="continue")

    async def execute(self, *, timeout: float = 300, poll: float = 1):
        try:
            async with asyncio.timeout(timeout):
                inventory = await self.docker.inventory()
                if not {"control", "tools", "worker", "db"}.issubset(
                    {item.service for item in inventory if item.running}
                ):
                    raise DockerError("Start Control, Tools, workers and Postgres before arming")
                if self.run_id:
                    self.controller = FaultController(
                        self.docker,
                        self.journal,
                        self.run_id,
                        self.hook,
                        container_lock=self.container_lock,
                    )
                    self.selected.set()
                await self.journal.record("armed", f"Scenario armed: {self.scenario}")
                self.armed_at = datetime.now(UTC)
                self.armed = True
                self.ready.set()
                await self.selected.wait()
                while True:
                    try:
                        await self.observe(self.run_id)
                        self.error = None
                        if self.run.status in {"succeeded", "failed"}:
                            await self.controller.close()
                            async with self.container_lock:
                                self.checks = evaluate(
                                    self.run, self.controller, list(self.child_runs.values())
                                )
                                missed = self.hook and (
                                    not self.controller.killed or self.controller.error
                                )
                                self.status = (
                                    "inconclusive"
                                    if missed
                                    else "passed"
                                    if all(self.checks.values())
                                    else "failed"
                                )
                            break
                    except httpx.HTTPError as exc:
                        self.error = str(exc)
                    await asyncio.sleep(poll)
        except Exception as exc:
            self.status, self.error = "inconclusive", str(exc) or "Scenario deadline exceeded"
        except asyncio.CancelledError:
            self.status, self.error = "inconclusive", "Runner stopped before verification"
            raise
        finally:
            self.armed = False
            self.ready.set()
            try:
                if self.controller:
                    await self.controller.close()
            except Exception as exc:
                self.status, self.error = "inconclusive", f"Recovery failed: {exc}"
            # An action already attributed to this scenario must finish before its report is saved.
            async with self.container_lock:
                await self.journal.record("verdict", f"Scenario {self.status}", detail=self.error)
                report = json.dumps(self.snapshot(), indent=2) + "\n"
                await asyncio.to_thread(self.journal.path.with_suffix(".json").write_text, report)

    async def _read_run(self, run_id: UUID) -> RunDetail:
        response = await self.client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        return RunDetail.model_validate(response.json())

    def _register_children(self, run: RunDetail):
        if self.controller:
            self.controller.run_ids.update(child.id for child in run.children)

    async def observe(self, run_id: UUID):
        self.run = await self._read_run(run_id)
        self._register_children(self.run)
        for child in self.run.children:
            self.child_runs[child.id] = await self._read_run(child.id)
        for run in [self.run, *self.child_runs.values()]:
            await self._read_checkpoints(run)
        self.observed_at = now()

    async def _read_checkpoints(self, run: RunDetail):
        for operation in run.operations:
            key = str(operation.id)
            if (
                operation.kind == "tool"
                and operation.tool_name != "delegate"
                and key not in self.checkpoints
            ):
                response = await self.client.get(f"/api/runs/{run.id}/operations/{key}/checkpoint")
                response.raise_for_status()
                if response.json() is not None:
                    self.checkpoints[key] = ToolCheckpoint.model_validate(
                        response.json()
                    ).model_dump(mode="json")
