import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from durable_agent.contracts import FailedOutcome, HeartbeatRequest, ModelResult
from durable_agent.control.app import create_app
from durable_agent.control.leases import heartbeat
from durable_agent.control.pause import set_pause
from durable_agent.control.views import detail
from durable_agent.db.models import Run
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from .test_leases import claimed, queued
from .test_reports import report, report_body
from .test_runtime import settings


async def pause(db, run_id, value=True):
    async with db.session() as session:
        await set_pause(session, run_id, value)
        await session.commit()


def test_pause_wins_claim_race_and_resume_preserves_pending_work(database, config):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            async with db.session() as session:
                before = await detail(session, run_id)
            async with db.session() as session:
                await set_pause(session, run_id, True)
                # A competing worker skips the row while pause holds its lock.
                assert await claimed(db) is None
                await session.commit()
            await pause(db, run_id)
            assert await claimed(db) is None
            async with db.session() as session:
                saved = await detail(session, run_id)
                assert saved.pause_requested and saved.status == "queued"
                assert saved.next_attempt_at == before.next_attempt_at
                assert saved.operations == before.operations
            # Other conversations can still execute while this one is paused.
            other_id = await queued(db, config)
            assert (await claimed(db)).run_id == other_id
            await pause(db, run_id, False)
            await pause(db, run_id, False)
            assigned = await claimed(db)
            assert assigned.operation_id == before.operations[0].id
            assert assigned.attempt_no == assigned.lease_generation == 1

    asyncio.run(exercise())


def test_claim_wins_pause_race_and_current_call_can_save_its_next_operation(database, config):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            assigned = await claimed(db)
            await pause(db, run_id)
            async with db.session() as session:
                run = await session.get(Run, run_id)
                renewed = await heartbeat(
                    session,
                    assigned.attempt_id,
                    HeartbeatRequest(worker_id=run.lease_owner, lease_generation=1),
                )
                await session.commit()
                assert renewed.deadline_at == assigned.deadline_at
            plan = ModelResult(
                kind="model",
                native_context=[],
                output={
                    "status": "completed",
                    "text": "",
                    "tool_calls": [
                        {"call_id": "read", "name": "read_file", "arguments": {"path": "a"}}
                    ],
                },
            )
            body = await report_body(db, assigned, plan)
            assert await report(db, assigned, body) == "recorded"
            assert await report(db, assigned, body) == "already_recorded"
            assert await claimed(db) is None
            async with db.session() as session:
                saved = await detail(session, run_id)
                assert saved.pause_requested and len(saved.operations) == 2
                assert saved.operations[0].result == plan
                assert saved.operations[1].attempts == []
            await pause(db, run_id, False)
            tool = await claimed(db)
            assert tool.operation_id == saved.operations[1].id
            assert tool.operation.request.input_version_id == config.initial_workspace_version_id

    asyncio.run(exercise())


@pytest.mark.parametrize("ending", ["success", "retry", "permanent", "exhausted"])
def test_pause_preserves_retry_delay_and_terminal_results_win(database, config, ending):
    async def exercise():
        async with database() as db:
            if ending == "exhausted":
                config.max_attempts = 1
            run_id = await queued(db, config)
            assigned = await claimed(db)
            await pause(db, run_id)
            body = await report_body(db, assigned)
            if ending != "success":
                body.outcome = FailedOutcome(
                    status="failed",
                    error={
                        "code": "offline",
                        "message": "Offline",
                        "retryable": ending != "permanent",
                        "retry_after_seconds": 60,
                    },
                )
            await report(db, assigned, body)
            async with db.session() as session:
                before = await detail(session, run_id)
                assert before.pause_requested == (ending == "retry")
                assert before.status == (
                    "retrying"
                    if ending == "retry"
                    else "succeeded"
                    if ending == "success"
                    else "failed"
                )
            await pause(db, run_id, False)
            assert await claimed(db) is None
            async with db.session() as session:
                after = await detail(session, run_id)
                assert after.next_attempt_at == before.next_attempt_at
                assert after.operations == before.operations
            if ending != "retry":
                await pause(db, run_id)
                async with db.session() as session:
                    assert not (await detail(session, run_id)).pause_requested

    asyncio.run(exercise())


def test_pause_http_reports_saved_state_and_keeps_conversation_busy(postgres_engine, monkeypatch):
    app = create_app(
        settings(postgres_engine),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"workspace_version_id": str(uuid4())})
        ),
    )
    with TestClient(app) as client:
        run = client.post("/api/runs", json={"submission_key": str(uuid4()), "prompt": "Hi"}).json()
        url = f"/api/runs/{run['id']}"
        assert run["pause_requested"] is False
        for _ in range(2):
            response = client.post(url + "/pause")
            assert response.status_code == 200 and response.json()["pause_requested"]
        assert client.get(f"/api/conversations/{run['id']}").json()["pause_requested"]
        assert client.get("/api/conversations").json()[0]["pause_requested"]
        assert client.get("/api/runs").json()[0]["pause_requested"]
        assert client.post("/internal/claims", json={"worker_id": str(uuid4())}).json() is None
        assert (
            client.post(
                f"/api/conversations/{run['id']}/runs",
                json={"submission_key": str(uuid4()), "prompt": "New", "after_run_id": run["id"]},
            ).status_code
            == 409
        )
        for _ in range(2):
            assert client.post(url + "/resume").json()["pause_requested"] is False
        for action in ("pause", "resume"):
            assert client.post(f"/api/runs/{uuid4()}/{action}").status_code == 404

        async def unavailable(session, run_id: UUID, paused):
            raise OperationalError("query", {}, OSError("database unavailable"))

        monkeypatch.setattr("durable_agent.control.app.set_pause", unavailable)
        assert client.post(url + "/pause").status_code == 503
