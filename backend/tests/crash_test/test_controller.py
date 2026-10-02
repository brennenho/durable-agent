import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from crash_test.controller import FaultController, Journal
from crash_test.docker import Container, DockerError
from durable_agent.crash_test import HookEvent


class Docker:
    def __init__(self):
        self.target = Container(
            "tools-id", "tools", "tools", "host", True, "2026-01-01T00:00:00Z", 0, "none"
        )
        self.kills, self.starts = 0, 0
        self.reached, self.release = asyncio.Event(), asyncio.Event()

    async def inventory(self):
        return [self.target]

    async def inspect(self, container):
        return self.target

    async def kill(self, target):
        self.kills += 1
        self.reached.set()
        await self.release.wait()
        self.target = replace(target, running=False, exit_code=137)
        return self.target

    async def start(self, target):
        self.starts += 1
        self.target = replace(target, running=True)
        return self.target


def event(name, operation, run=None, generation=1, **kwargs):
    return HookEvent(
        name=name,
        service="worker" if run else "tools",
        instance="host",
        pid=1,
        run_id=run,
        operation_id=operation,
        lease_generation=generation,
        **kwargs,
    )


def test_rule_is_scoped_consumed_once_and_survives_request_cancellation(tmp_path):
    async def exercise():
        docker, journal, run, operation = (
            Docker(),
            Journal(tmp_path / "events.jsonl"),
            uuid4(),
            uuid4(),
        )
        controller = FaultController(docker, journal, run, "tools.after_commit")
        await controller.observe(event("tools.after_commit", operation))
        await controller.observe(event("worker.after_claim", operation, uuid4()))
        assert not journal.events
        await controller.observe(event("worker.after_claim", operation, run))
        await controller.observe(event("tools.after_commit", operation, uuid4()))
        assert docker.kills == 0
        stale = event(
            "tools.after_commit", operation, observed_at=datetime.now(UTC) - timedelta(seconds=20)
        )
        await controller.observe(stale)
        assert docker.kills == 0
        trigger = event("tools.after_commit", operation)
        pending = asyncio.create_task(controller.observe(trigger))
        await asyncio.wait_for(docker.reached.wait(), 1)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await controller.observe(trigger)
        await controller.observe(event("tools.after_commit", operation))
        docker.release.set()
        await controller.close()
        assert docker.kills == docker.starts == 1
        assert controller.killed and controller.error is None
        kinds = [e["kind"] for e in journal.events]
        assert kinds.index("kill_requested") < kinds.index("kill_observed") < kinds.index("restart")
        assert [
            json.loads(line) for line in journal.path.read_text().splitlines()
        ] == journal.events

    asyncio.run(exercise())


def test_failed_kill_is_not_reported_as_observed(tmp_path):
    async def exercise():
        docker, run, operation = Docker(), uuid4(), uuid4()

        async def failed(target):
            raise DockerError("Docker offline")

        docker.kill = failed
        controller = FaultController(
            docker, Journal(tmp_path / "events.jsonl"), run, "tools.after_commit"
        )
        await controller.observe(event("worker.after_claim", operation, run, generation=2))
        await controller.observe(event("tools.after_commit", operation, generation=1))
        assert controller.trigger is None
        await controller.observe(event("tools.after_commit", operation, generation=2))
        assert not controller.killed and controller.error == "Docker offline"
        assert "kill_observed" not in [e["kind"] for e in controller.journal.events]

    asyncio.run(exercise())


def test_close_disarms_hook_waiting_on_journal(tmp_path):
    async def exercise():
        docker, run, operation = Docker(), uuid4(), uuid4()
        controller = FaultController(
            docker, Journal(tmp_path / "events.jsonl"), run, "tools.after_commit"
        )
        await controller.observe(event("worker.after_claim", operation, run))
        async with controller.journal.lock:
            pending = asyncio.create_task(
                controller.observe(event("tools.after_commit", operation))
            )
            await asyncio.sleep(0)
            await controller.close()
        await pending
        assert controller.trigger is None and docker.kills == 0

    asyncio.run(exercise())


def test_superseded_hook_cannot_kill_after_target_resolution(tmp_path):
    async def exercise():
        docker, run, operation = Docker(), uuid4(), uuid4()
        selected, release = asyncio.Event(), asyncio.Event()

        async def inventory():
            selected.set()
            await release.wait()
            return [docker.target]

        docker.inventory = inventory
        controller = FaultController(
            docker, Journal(tmp_path / "events.jsonl"), run, "tools.after_commit"
        )
        await controller.observe(event("worker.after_claim", operation, run))
        pending = asyncio.create_task(controller.observe(event("tools.after_commit", operation)))
        await selected.wait()
        await controller.observe(event("worker.after_claim", operation, run, generation=2))
        release.set()
        await pending
        assert docker.kills == 0 and not controller.killed
        assert "superseded" in controller.error

    asyncio.run(exercise())


def test_fault_waiting_for_manual_action_expires_with_the_hook_window(tmp_path):
    async def exercise():
        docker, run, operation = Docker(), uuid4(), uuid4()
        controller = FaultController(
            docker, Journal(tmp_path / "events.jsonl"), run, "tools.after_commit"
        )
        await controller.observe(event("worker.after_claim", operation, run))
        async with controller.container_lock:
            await controller.observe(
                event(
                    "tools.after_commit",
                    operation,
                    observed_at=datetime.now(UTC) - timedelta(seconds=7.8),
                )
            )
        assert docker.kills == 0 and not controller.killed
        assert "expired while waiting" in controller.error

    asyncio.run(exercise())


def test_child_hooks_are_scoped_to_authoritative_run_ids(tmp_path):
    async def exercise():
        docker, root, child, foreign, operation = Docker(), uuid4(), uuid4(), uuid4(), uuid4()
        docker.release.set()
        controller = FaultController(
            docker, Journal(tmp_path / "events.jsonl"), root, "tools.after_commit"
        )
        await controller.observe(event("worker.after_claim", operation, child))
        await controller.observe(event("tools.after_commit", operation))
        assert not controller.journal.events and docker.kills == 0

        # The runner adds IDs only after reading the parent's saved child relationships.
        controller.run_ids.add(child)
        await controller.observe(event("worker.after_claim", uuid4(), foreign))
        await controller.observe(event("worker.after_claim", operation, child, generation=2))
        await controller.observe(event("tools.after_commit", operation, generation=1))
        assert controller.trigger is None
        await controller.observe(event("tools.after_commit", operation, generation=2))
        assert controller.killed and docker.kills == docker.starts == 1
        assert len(controller.operations) == 1
        assert controller.journal.events[0]["hook"]["run_id"] == str(child)

    asyncio.run(exercise())


def test_coordinator_commit_kills_control_without_a_worker_attempt(tmp_path):
    async def exercise(hook):
        docker, run, operation = Docker(), uuid4(), uuid4()
        docker.target = replace(docker.target, service="control")
        docker.release.set()
        controller = FaultController(docker, Journal(tmp_path / f"{hook}.jsonl"), run, hook)
        committed = HookEvent(
            name=hook,
            service="control",
            instance="host",
            pid=1,
            run_id=run,
            operation_id=operation,
        )
        for invalid in [
            committed.model_copy(update={"run_id": uuid4()}),
            committed.model_copy(update={"service": "worker"}),
            committed.model_copy(update={"attempt_id": uuid4()}),
            committed.model_copy(update={"lease_generation": 1}),
            committed.model_copy(update={"observed_at": datetime.now(UTC) - timedelta(seconds=20)}),
        ]:
            await controller.observe(invalid.model_copy(update={"event_id": uuid4()}))
            assert docker.kills == 0
        controller.children_by_operation[operation] = (uuid4(),)
        await controller.observe(committed)
        await controller.observe(committed.model_copy(update={"event_id": uuid4()}))
        await controller.close()
        assert controller.killed and controller.error is None
        assert docker.kills == docker.starts == 1
        assert not controller.operations

    for hook in ["control.after_children_commit", "control.after_join_commit"]:
        asyncio.run(exercise(hook))


def test_unclaimed_worker_hook_without_a_generation_cannot_trigger(tmp_path):
    async def exercise():
        docker, run = Docker(), uuid4()
        controller = FaultController(
            docker, Journal(tmp_path / "events.jsonl"), run, "worker.before_report"
        )
        await controller.observe(event("worker.before_report", uuid4(), run, generation=None))
        assert controller.trigger is None and docker.kills == 0

    asyncio.run(exercise())
