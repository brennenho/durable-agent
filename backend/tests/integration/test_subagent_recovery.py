"""Real Control/Tools/Worker recovery; only OpenAI responses are recorded fixtures."""

import asyncio
import json
from collections import Counter
from uuid import uuid4

import httpx
import pytest
from durable_agent.control.runtime import Settings
from durable_agent.db.models import Run
from durable_agent.db.session import Database
from durable_agent.tools import execution
from sqlalchemy import func, text, update

from .test_conversation_flow import answer, call, response, running_services


async def wait_detail(api, run_id, condition):
    async with asyncio.timeout(10):
        while True:
            result = await api.get(f"/api/runs/{run_id}")
            assert result.status_code == 200, result.text
            detail = result.json()
            if condition(detail):
                return detail
            await asyncio.sleep(0.02)


async def run_next(services, worker):
    claimed_at = asyncio.get_running_loop().time()
    assignment = await services.claim(worker.worker_id)
    assert assignment is not None
    await worker.run_assignment(assignment, claimed_at=claimed_at)
    return assignment


@pytest.mark.parametrize("child_fails", [False, True], ids=["reports", "mixed-report-and-error"])
def test_children_recover_saved_tools_and_resume_parent_once(
    postgres_engine, monkeypatch, child_fails
):
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
    delegate = {
        "type": "function_call",
        "id": "fc_delegate",
        "call_id": "delegate",
        "name": "delegate",
        "arguments": json.dumps(
            {"tasks": [{"name": name, "prompt": name} for name in ("Alpha", "Beta")]}
        ),
        "status": "completed",
    }
    requests, executions, lost_acks = [], [], []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        prompt = [item["content"] for item in body["input"] if item.get("role") == "user"][-1]
        outputs = [item for item in body["input"] if item.get("type") == "function_call_output"]
        if prompt == "Root":
            output = (
                [call("seed", "Root\n"), delegate] if not outputs else answer("Combined reports")
            )
        elif prompt == "Follow up":
            output = answer("No repeated children")
        elif not outputs:
            output = [call(f"write_{prompt}", f"{prompt}\n")]
        elif prompt == "Beta" and child_fails:
            return httpx.Response(400, json={"error": {"message": "Fixture rejects Beta"}})
        else:
            output = answer(f"Report from {prompt}")
        return httpx.Response(200, json=response(output, str(len(requests))))

    execute_file_tool = execution.execute_file_tool

    def counted(*args):
        executions.append(args[1])
        return execute_file_tool(*args)

    monkeypatch.setattr(execution, "execute_file_tool", counted)

    async def exercise():
        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            created = await api.post(
                "/api/runs", json={"submission_key": str(uuid4()), "prompt": "Root"}
            )
            assert created.status_code == 200, created.text
            root_id = created.json()["id"]
            await run_next(services, worker)
            await run_next(services, worker)
            parent = await wait_detail(api, root_id, lambda run: len(run["children"]) == 2)
            assert parent["status"] == "waiting"
            children = {child["name"]: child["id"] for child in parent["children"]}
            models = [await services.claim(worker.worker_id) for _ in range(2)]
            assert {str(item.run_id) for item in models} == set(children.values())
            for item in models:
                assert item.operation.request.previous_turns == []
                assert "delegate" not in [schema["name"] for schema in item.config.tool_schemas]
            await asyncio.gather(
                *(
                    worker.run_assignment(item, claimed_at=asyncio.get_running_loop().time())
                    for item in models
                )
            )
            tools = [await services.claim(worker.worker_id) for _ in range(2)]
            by_run = {str(item.run_id): item for item in tools}
            interrupted = by_run[children["Alpha"]]
            saved = await services.execute_tool(interrupted, timeout_seconds=5)
            await worker.run_assignment(
                by_run[children["Beta"]], claimed_at=asyncio.get_running_loop().time()
            )
            paused = await api.post(f"/api/runs/{root_id}/pause")
            assert paused.status_code == 200, paused.text
            assert await services.claim(worker.worker_id) is None
            delegation_id = parent["operations"][-1]["id"]
            checkpoint = await api.get(f"/api/runs/{root_id}/operations/{delegation_id}/checkpoint")
            assert checkpoint.status_code == 200 and checkpoint.json() is None

        # Alpha saved its workspace, but its worker vanished before reporting it.
        db = Database(database_url)
        try:
            async with db.session() as session:
                await session.execute(
                    update(Run)
                    .where(Run.id == interrupted.run_id)
                    .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'"))
                )
                await session.commit()
        finally:
            await db.close()

        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            await wait_detail(api, children["Alpha"], lambda run: run["status"] == "retrying")
            assert await services.claim(worker.worker_id) is None
            parent = (await api.get(f"/api/runs/{root_id}")).json()
            assert {child["name"]: child["id"] for child in parent["children"]} == children
            assert parent["pause_requested"] is True
            resumed = await api.post(f"/api/runs/{root_id}/resume")
            assert resumed.status_code == 200, resumed.text
            assignments = []
            async with asyncio.timeout(15):
                while True:
                    parent = (await api.get(f"/api/runs/{root_id}")).json()
                    if parent["status"] == "succeeded":
                        break
                    claimed_at = asyncio.get_running_loop().time()
                    assignment = await services.claim(worker.worker_id)
                    if assignment is None:
                        await asyncio.sleep(0.02)
                        continue
                    assignments.append(assignment)
                    await worker.run_assignment(assignment, claimed_at=claimed_at)
            retried = next(
                item for item in assignments if item.operation_id == interrupted.operation_id
            )
            assert retried.attempt_no == 2
            assert retried.operation.request == interrupted.operation.request
            delegation = next(op for op in parent["operations"] if op["id"] == delegation_id)
            assert delegation["attempts"] == []
            results = delegation["result"]["output"]["value"]["children"]
            assert [result["name"] for result in results] == ["Alpha", "Beta"]
            assert results[0]["report"] == "Report from Alpha"
            assert results[1]["status"] == ("failed" if child_fails else "succeeded")
            assert (results[1]["error"] is not None) == child_fails
            delivered = next(
                item
                for item in requests[-1]["input"]
                if item.get("type") == "function_call_output" and item["call_id"] == "delegate"
            )
            assert json.loads(delivered["output"])["value"]["children"] == results
            assert parent["workspace_version_id"] == delegation["result"]["workspace_version_id"]
            assert Counter(executions) == {"write_file": 3}
            for name, run_id in [("Root", root_id), *children.items()]:
                detail = (await api.get(f"/api/runs/{run_id}")).json()
                file = await api.get(
                    f"/api/runs/{run_id}/file",
                    params={"version_id": detail["workspace_version_id"], "path": "report.md"},
                )
                assert file.content == f"{name}\n".encode()
                if name == "Alpha":
                    assert detail["workspace_version_id"] == str(saved.result.workspace_version_id)
                    assert [
                        attempt["status"] for attempt in detail["operations"][1]["attempts"]
                    ] == ["unknown", "accepted"]
            assert len((await api.get("/api/conversations")).json()) == 1
            assert len(requests) == 6
            assert len(lost_acks) == 1

        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            assert (await api.get(f"/api/runs/{root_id}")).json() == parent
            assert await services.claim(worker.worker_id) is None
            followup = await api.post(
                f"/api/conversations/{root_id}/runs",
                json={
                    "submission_key": str(uuid4()),
                    "after_run_id": root_id,
                    "prompt": "Follow up",
                },
            )
            assert followup.status_code == 200, followup.text
            await run_next(services, worker)
            assert await services.claim(worker.worker_id) is None
            assert (await api.get(f"/api/runs/{root_id}")).json() == parent
            assert len(requests) == 7
            assert Counter(executions) == {"write_file": 3}

    asyncio.run(exercise())
