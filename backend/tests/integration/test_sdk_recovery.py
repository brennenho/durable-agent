"""Custom agent state and files recover through the real service boundaries."""

import asyncio
from uuid import uuid4

import pytest
from durable_agent.control.runtime import Settings
from durable_agent.db.models import Run
from durable_agent.db.session import Database
from durable_agent.sdk import Done, Next, ToolCall
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.registration import agent
from durable_agent.tools import execution
from sqlalchemy import func, text, update

from .test_conversation_flow import running_services
from .test_subagent_recovery import run_next, wait_detail


@agent(name="saved_counter", version="1")
class SavedCounter:
    def start(self, ctx):
        return Next(
            state=ctx.state if ctx.state is not None else 0,
            step=ToolCall(name="write_file", arguments={"path": "note.txt", "content": ctx.input}),
        )

    def advance(self, ctx, result):
        assert result.error is None
        assert len(ctx.history) == ctx.state
        return Done(output={"accepted": ctx.state + 1}, state=ctx.state + 1)


app = AgentApp(agents=[SavedCounter])


def test_sdk_recovers_saved_tool_state_across_service_restart(postgres_engine, monkeypatch):
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
        model="unused",
        image_digest="sha256:" + "a" * 64,
        agent_app=f"{__name__}:app",
        agent_build_id="recovery-fixture-1",
    )
    executions, lost_acks = [], []
    execute = execution.execute_file_tool

    def counted(*args):
        executions.append(args[1])
        return execute(*args)

    def no_model(request):
        pytest.fail(f"Tool-only agent unexpectedly called the model: {request.url}")

    monkeypatch.setattr(execution, "execute_file_tool", counted)

    async def file_bytes(api, run_id):
        detail = (await api.get(f"/api/runs/{run_id}")).json()
        response = await api.get(
            f"/api/runs/{run_id}/file",
            params={"path": "note.txt", "version_id": detail["workspace_version_id"]},
        )
        assert response.status_code == 200, response.text
        return response.content

    async def exercise():
        submission = {"agent": "saved_counter", "input": "original", "submission_key": str(uuid4())}
        async with running_services(settings, no_model, lost_acks) as (api, services, worker):
            created = await api.post("/api/runs", json=submission)
            assert created.status_code == 200, created.text
            run_id = created.json()["id"]
            assignment = await services.claim(worker.worker_id)
            assert str(assignment.run_id) == run_id
            assert assignment.operation.kind == "tool"
            saved = await services.execute_tool(assignment, timeout_seconds=5)
            # Tools committed, but this worker never reported its result to Control.
            assert (await api.post(f"/api/runs/{run_id}/pause")).status_code == 200
            assert (await api.get(f"/api/runs/{run_id}/files")).json()["entries"] == []
            assert await services.claim(worker.worker_id) is None

        db = Database(database_url)
        try:
            async with db.session() as session:
                await session.execute(
                    update(Run)
                    .where(Run.id == assignment.run_id)
                    .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'"))
                )
                await session.commit()
        finally:
            await db.close()

        async with running_services(settings, no_model, lost_acks) as (api, services, worker):
            repeated = await api.post("/api/runs", json=submission)
            assert repeated.status_code == 200 and repeated.json()["id"] == run_id
            detail = await wait_detail(api, run_id, lambda detail: detail["status"] == "retrying")
            assert detail["pause_requested"]
            assert await services.claim(worker.worker_id) is None
            assert (await api.post(f"/api/runs/{run_id}/resume")).status_code == 200
            async with asyncio.timeout(10):
                while True:
                    retry = await services.claim(worker.worker_id)
                    if retry is not None:
                        break
                    await asyncio.sleep(0.02)
            assert retry.operation_id == assignment.operation_id
            assert retry.operation.request == assignment.operation.request
            assert retry.attempt_no == 2
            await worker.run_assignment(retry, claimed_at=asyncio.get_running_loop().time())
            detail = (await api.get(f"/api/runs/{run_id}")).json()
            assert detail["status"] == "succeeded" and detail["output"] == {"accepted": 1}
            assert detail["workspace_version_id"] == str(saved.result.workspace_version_id)
            assert len(detail["operations"]) == 1
            assert [a["status"] for a in detail["operations"][0]["attempts"]] == [
                "unknown",
                "accepted",
            ]
            assert executions == ["write_file"]
            assert len(lost_acks) == 1
            assert await services.claim(worker.worker_id) is None

        async with running_services(settings, no_model, lost_acks) as (api, services, worker):
            original = (await api.get(f"/api/runs/{run_id}")).json()
            assert original == detail
            followup = await api.post(
                f"/api/conversations/{run_id}/runs",
                json={"after_run_id": run_id, "input": "followup", "submission_key": str(uuid4())},
            )
            assert followup.status_code == 200, followup.text
            followup_id = followup.json()["id"]
            assert await file_bytes(api, followup_id) == b"original"
            await run_next(services, worker)
            continued = (await api.get(f"/api/runs/{followup_id}")).json()
            assert continued["status"] == "succeeded" and continued["output"] == {"accepted": 2}
            fork = await api.post(
                f"/api/runs/{run_id}/forks",
                json={
                    "after_operation_id": str(assignment.operation_id),
                    "input": "branch",
                    "submission_key": str(uuid4()),
                },
            )
            assert fork.status_code == 200, fork.text
            branch_id = fork.json()["latest_run_id"]
            assert await file_bytes(api, branch_id) == b"original"
            await run_next(services, worker)
            branched = (await api.get(f"/api/runs/{branch_id}")).json()
            assert branched["status"] == "succeeded" and branched["output"] == {"accepted": 2}
            assert branched["conversation_id"] != run_id
            assert await file_bytes(api, run_id) == b"original"
            assert await file_bytes(api, followup_id) == b"followup"
            assert await file_bytes(api, branch_id) == b"branch"
            assert await services.claim(worker.worker_id) is None
            assert executions == ["write_file"] * 3

    asyncio.run(exercise())
