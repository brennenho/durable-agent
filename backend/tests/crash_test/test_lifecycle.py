import asyncio
import json
from uuid import uuid4

import httpx
from crash_test.app import create_app
from crash_test.controller import FaultController, Journal
from crash_test.room import ControlRoom
from crash_test.runner import ScenarioRun
from durable_agent.control.worker_pool import DockerWorkers, WorkerPoolError
from durable_agent.crash_test import CrashTestHooks

from ..control.test_worker_pool import Engine
from .test_runner import Docker


def test_worker_lifecycle_reaches_snapshot_only_after_successful_docker_actions(tmp_path):
    async def exercise():
        engine = Engine()
        fail_start = False

        def docker(request):
            if fail_start and request.url.path.endswith("/start"):
                return httpx.Response(503)
            return engine.handle(request)

        async with httpx.AsyncClient(base_url="http://control") as control:
            room = ControlRoom(Docker(), control, tmp_path)
            room.docker.project = "test"
            app = create_app(room, token="t" * 32)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    base_url="http://crash",
                    transport=httpx.ASGITransport(app=app),
                    headers={"Authorization": "Bearer " + "t" * 32},
                ) as observer,
                httpx.AsyncClient(
                    base_url="http://docker", transport=httpx.MockTransport(docker)
                ) as client,
            ):
                pool = DockerWorkers(client, "test", 4, hooks=CrashTestHooks("control", observer))
                await pool.resize(3)
                await pool.resize(3)
                engine.workers["1"]["State"] = "exited"
                await pool.resize(2)
                events = (await observer.get("/snapshot")).json()["controls"]
                assert [event["label"] for event in events] == [
                    "Worker created",
                    "Worker started",
                    "Worker restarted",
                    "Worker removed",
                ]
                assert events[2]["hook"]["container_id"] == "1"
                assert events[2]["hook"]["worker_slot"] == 1
                assert "test-key" not in json.dumps(events)
                fail_start = True
                try:
                    await pool.resize(3)
                except WorkerPoolError:
                    pass
                else:
                    raise AssertionError("Docker start failure must propagate")
                assert room.journal.events[-1]["label"] == "Worker created"
                assert len(room.journal.events) == 5

    asyncio.run(exercise())


def test_sandbox_starts_are_saved_without_scenarios_and_still_reach_active_scenarios(tmp_path):
    async def exercise():
        async with httpx.AsyncClient(base_url="http://control") as control:
            room = ControlRoom(Docker(), control, tmp_path)
            room.docker.project = "test"
            app = create_app(room, token="t" * 32)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    base_url="http://crash",
                    transport=httpx.ASGITransport(app=app),
                    headers={"Authorization": "Bearer " + "t" * 32},
                ) as client,
            ):
                hooks = CrashTestHooks("tools", client)
                operation_id, run_id = uuid4(), uuid4()
                await hooks.hit("sandbox.started", operation_id=operation_id, sandbox_id="first")
                room.current = ScenarioRun(
                    "baseline", room.docker, control, tmp_path, run_id=run_id
                )
                room.current.armed = True
                room.current.controller = FaultController(
                    room.docker, room.current.journal, run_id, None
                )
                room.current.controller.operations[operation_id] = 2
                await hooks.hit(
                    "sandbox.started",
                    operation_id=operation_id,
                    sandbox_id="replacement",
                    lease_generation=2,
                )
                snapshot = (await client.get("/snapshot")).json()
                events = snapshot["controls"]
                assert [event["hook"]["sandbox_id"] for event in events] == ["first", "replacement"]
                assert events[-1]["operation_id"] == str(operation_id)
                assert "lease generation 2" in events[-1]["detail"]
                assert (
                    snapshot["current"]["events"][0]["hook"]["event_id"]
                    == events[-1]["hook"]["event_id"]
                )
                await hooks.hit(
                    "control.worker_started", container_id="wrong-service", worker_slot=1
                )
                assert len(room.journal.events) == 2

    asyncio.run(exercise())


def test_controls_keep_recent_events_and_append_full_history(tmp_path):
    async def exercise():
        journal = Journal(tmp_path / "controls.jsonl", rolling=True)
        journal.events = [{"label": f"old-{i}"} for i in range(2000)]
        await journal.record("lifecycle", "Worker started")
        assert len(journal.events) == 2000
        assert journal.events[0]["label"] == "old-1"
        assert json.loads(journal.path.read_text())["label"] == "Worker started"

    asyncio.run(exercise())
