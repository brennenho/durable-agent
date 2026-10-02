import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from crash_test.contracts import ContainerAction
from crash_test.docker import DockerError
from crash_test.room import ControlRoom
from durable_agent.crash_test import HookEvent
from fastapi import HTTPException

from .test_runner import Docker, completed_run


@pytest.mark.parametrize("automatic_first", [True, False])
def test_manual_and_automatic_actions_cannot_race_or_deadlock_shutdown(tmp_path, automatic_first):
    async def exercise():
        docker = Docker()
        entered, release = asyncio.Event(), asyncio.Event()
        kill = docker.kill

        async def delayed_kill(target):
            entered.set()
            await release.wait()
            return await kill(target)

        async def blocked(request):
            await asyncio.Event().wait()

        docker.kill = delayed_kill
        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(blocked)
        ) as client:
            room = ControlRoom(docker, client, tmp_path)
            run_id = uuid4()
            await room.start("saved-response-lost", run_id)
            controller = room.current.controller
            claim = HookEvent(
                name="worker.after_claim",
                service="worker",
                instance="worker",
                pid=1,
                run_id=run_id,
                operation_id=uuid4(),
                attempt_id=uuid4(),
                lease_generation=1,
            )
            await controller.observe(claim)
            trigger = claim.model_copy(
                update={
                    "event_id": uuid4(),
                    "name": "tools.after_commit",
                    "service": "tools",
                    "instance": "tools",
                }
            )
            target = docker.containers["tools"]
            body = ContainerAction(id=target.id, started_at=target.started_at, action="kill")
            first = asyncio.create_task(
                controller.observe(trigger) if automatic_first else room.action(body)
            )
            await entered.wait()
            if automatic_first:
                with pytest.raises(HTTPException) as conflict:
                    await room.action(body)
                assert conflict.value.status_code == 409
                closing = asyncio.create_task(room.close())
                await asyncio.sleep(0)
                assert not closing.done()
                release.set()
                await first
                await closing
                assert docker.containers["tools"].running
                assert not room.current.snapshot()["manual_intervention"]
            else:
                automatic = asyncio.create_task(controller.observe(trigger))
                while controller.trigger is None:
                    await asyncio.sleep(0)
                assert not automatic.done()
                release.set()
                await asyncio.gather(first, automatic)
                # The automatic fault must not restart a container the operator stopped.
                assert not docker.containers["tools"].running
                assert not controller.killed and controller.error
                await room.close()
                report = json.loads(room.current.journal.path.with_suffix(".json").read_text())
                assert report["manual_intervention"]
                assert any(e["kind"] == "kill_observed" for e in report["events"])

    async def bounded():
        async with asyncio.timeout(3):
            await exercise()

    asyncio.run(bounded())


@pytest.mark.parametrize("fails", [False, True])
def test_report_waits_for_manual_action_outcome_before_finishing(tmp_path, fails):
    async def exercise():
        docker, run = Docker(), completed_run()
        finish, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        kill = docker.kill

        async def delayed_kill(target):
            entered.set()
            await release.wait()
            if fails:
                raise DockerError("Docker unavailable")
            return await kill(target)

        async def response(request):
            await finish.wait()
            if request.url.path.endswith("/checkpoint"):
                return httpx.Response(200, content="null")
            return httpx.Response(200, json=run)

        docker.kill = delayed_kill
        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(response)
        ) as client:
            room = ControlRoom(docker, client, tmp_path)
            await room.start("baseline", UUID(run["id"]))
            target = docker.containers["frontend"]
            action = asyncio.create_task(
                room.action(
                    ContainerAction(id=target.id, started_at=target.started_at, action="kill")
                )
            )
            await entered.wait()
            finish.set()
            while room.current.run is None:
                await asyncio.sleep(0)
            assert room.current.status == "running" and not room.task.done()
            report_path = room.current.journal.path.with_suffix(".json")
            assert not report_path.exists()
            release.set()
            if fails:
                with pytest.raises(DockerError, match="Docker unavailable"):
                    await action
            else:
                await action
            await room.task
            report = json.loads(report_path.read_text())
            assert report == room.current.snapshot()
            assert report["manual_intervention"] and report["status"] == "passed"
            assert [e["kind"] for e in report["events"]] == [
                "armed",
                "manual_action",
                "fault_error" if fails else "kill_observed",
                "verdict",
            ]
            assert not room.journal.events

    async def bounded():
        async with asyncio.timeout(3):
            await exercise()

    asyncio.run(bounded())
