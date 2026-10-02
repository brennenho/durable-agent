"""Pause/resume uses the same persisted operations and tool recovery path."""

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from durable_agent.control.runtime import Settings
from durable_agent.db.models import Run
from durable_agent.db.session import Database
from durable_agent.tools import execution
from sqlalchemy import func, text, update

from .test_conversation_flow import answer, call, response, running_services


@pytest.fixture
def pause_flow(postgres_engine, monkeypatch):
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    for name in (
        "WORKSPACE_INPUT_DIR",
        "SANDBOX_WORKSPACE_ROOT",
        "SANDBOX_IMAGE_DIGEST",
        "CRASH_TEST_HOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        database_url=database_url,
        tools_url="http://tools",
        model="fixture",
        image_digest="sha256:" + "a" * 64,
    )
    requests, executions = [], []
    plan = [
        response([call("saved", "Saved before resume\n")], "plan"),
        response(answer("Finished after resume"), "final"),
    ]

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=deepcopy(plan.pop(0)))

    execute_file_tool = execution.execute_file_tool

    def counted_file_tool(*args):
        executions.append(args[1])
        return execute_file_tool(*args)

    monkeypatch.setattr(execution, "execute_file_tool", counted_file_tool)
    return settings, provider, requests, executions


async def start_tool(api, services, worker):
    created = await api.post(
        "/api/runs", json={"submission_key": str(uuid4()), "prompt": "Write a report"}
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    claimed_at = asyncio.get_running_loop().time()
    model = await services.claim(worker.worker_id)
    assert model is not None and model.operation.kind == "model"
    await worker.run_assignment(model, claimed_at=claimed_at)
    claimed_at = asyncio.get_running_loop().time()
    tool = await services.claim(worker.worker_id)
    assert tool is not None and tool.operation.kind == "tool"
    paused = await api.post(f"/api/runs/{run_id}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "running"
    assert paused.json()["pause_requested"] is True
    return run_id, tool, claimed_at


async def finish_run(api, services, worker, run_id, requests):
    claimed_at = asyncio.get_running_loop().time()
    model = await services.claim(worker.worker_id)
    assert model is not None and model.operation.kind == "model"
    await worker.run_assignment(model, claimed_at=claimed_at)
    saved = (await api.get(f"/api/runs/{run_id}")).json()
    assert saved["status"] == "succeeded"
    assert saved["pause_requested"] is False
    assert saved["final_answer"] == "Finished after resume"
    assert len(saved["operations"]) == 3
    assert len(saved["operations"][0]["attempts"]) == 1
    assert len(requests) == 2
    returned = next(
        item for item in requests[1]["input"] if item.get("type") == "function_call_output"
    )
    assert returned["call_id"] == "saved"
    assert json.loads(returned["output"]) == {
        "status": "ok",
        "value": {"bytes_written": len("Saved before resume\n")},
    }
    download = await api.get(
        f"/api/runs/{run_id}/file",
        params={"version_id": saved["workspace_version_id"], "path": "report.md"},
    )
    assert download.status_code == 200, download.text
    assert download.content == b"Saved before resume\n"
    assert await services.claim(worker.worker_id) is None
    return saved


def test_pause_survives_restart_after_current_tool_finishes(pause_flow):
    settings, provider, requests, executions = pause_flow
    lost_acks = []

    async def exercise():
        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            run_id, tool, claimed_at = await start_tool(api, services, worker)
            await worker.run_assignment(tool, claimed_at=claimed_at)
            paused = (await api.get(f"/api/runs/{run_id}")).json()
            assert paused["status"] == "queued"
            assert paused["pause_requested"] is True
            assert paused["operations"][1]["attempts"][0]["status"] == "accepted"
            assert paused["operations"][2]["attempts"] == []
            assert await services.claim(worker.worker_id) is None

        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            assert (await api.get(f"/api/runs/{run_id}")).json() == paused
            assert await services.claim(worker.worker_id) is None
            resumed = await api.post(f"/api/runs/{run_id}/resume")
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["pause_requested"] is False
            final = await finish_run(api, services, worker, run_id, requests)
            assert [op["id"] for op in final["operations"]] == [
                op["id"] for op in paused["operations"]
            ]
            assert final["workspace_version_id"] == paused["workspace_version_id"]
            assert executions == ["write_file"]

    asyncio.run(exercise())


@pytest.mark.parametrize("tool_committed", [False, True], ids=["before-save", "after-save"])
def test_paused_crash_recovers_same_tool_only_after_resume(pause_flow, tool_committed):
    settings, provider, requests, executions = pause_flow
    lost_acks = []

    async def exercise():
        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            run_id, first, _ = await start_tool(api, services, worker)
            checkpoint_path = f"/api/runs/{run_id}/operations/{first.operation_id}/checkpoint"
            # Simulate losing the worker before Control receives a tool report.
            original = (
                await services.execute_tool(first, timeout_seconds=5) if tool_committed else None
            )
            checkpoint = (await api.get(checkpoint_path)).json()
            assert (checkpoint is not None) == tool_committed
            assert (await api.get(f"/api/runs/{run_id}")).json()["operations"][1]["result"] is None

        # Expire the abandoned lease while services are down; startup recovery handles it.
        db = Database(settings.database_url)
        try:
            async with db.session() as session:
                await session.execute(
                    update(Run)
                    .where(Run.id == first.run_id)
                    .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'"))
                )
                await session.commit()
        finally:
            await db.close()

        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            async with asyncio.timeout(5):
                while True:
                    assert await services.claim(worker.worker_id) is None
                    paused = (await api.get(f"/api/runs/{run_id}")).json()
                    if paused["status"] == "retrying" and datetime.now(
                        UTC
                    ) >= datetime.fromisoformat(paused["next_attempt_at"]):
                        break
                    await asyncio.sleep(0.05)
            assert paused["pause_requested"] is True
            interrupted = paused["operations"][1]
            assert interrupted["id"] == str(first.operation_id)
            assert interrupted["result"] is None
            assert len(interrupted["attempts"]) == 1
            assert interrupted["attempts"][0]["status"] == "unknown"
            assert interrupted["attempts"][0]["error"]["code"] == "lease_expired"
            assert len(requests) == 1
            assert (await api.get(checkpoint_path)).json() == checkpoint
            resumed = await api.post(f"/api/runs/{run_id}/resume")
            assert resumed.status_code == 200, resumed.text
            claimed_at = asyncio.get_running_loop().time()
            retry = await services.claim(worker.worker_id)
            assert retry is not None
            assert retry.operation_id == first.operation_id
            assert retry.operation == first.operation
            assert retry.config == first.config
            assert retry.attempt_id != first.attempt_id
            assert retry.attempt_no == first.attempt_no + 1
            assert retry.lease_generation == first.lease_generation + 1
            await worker.run_assignment(retry, claimed_at=claimed_at)
            final = await finish_run(api, services, worker, run_id, requests)
            assert [a["status"] for a in final["operations"][1]["attempts"]] == [
                "unknown",
                "accepted",
            ]
            if original is not None:
                assert final["operations"][1]["result"] == original.result.model_dump(mode="json")
                assert (await api.get(checkpoint_path)).json() == checkpoint
            assert executions == ["write_file"]

    asyncio.run(exercise())
