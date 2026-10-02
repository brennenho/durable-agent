import asyncio
import json
import sys

import httpx
import pytest
from durable_agent.crash_test import CrashTestHooks
from durable_agent.db.models import ToolRequest
from durable_agent.tools import execution
from durable_agent.tools.app import app
from fastapi.testclient import TestClient
from sqlalchemy import select

from .test_api import configured as configured
from .test_api import configured_sandbox as configured_sandbox
from .test_api import tool_request


@pytest.mark.parametrize("boundary", ["tools.before_publish", "tools.after_commit"])
def test_interrupt_around_commit_preserves_retry_semantics(configured, monkeypatch, boundary):
    calls, events = [], []
    execute = execution.execute_file_tool

    def count_execution(*args):
        calls.append(True)
        return execute(*args)

    monkeypatch.setattr(execution, "execute_file_tool", count_execution)

    async def exercise():
        armed = True
        async with app.router.lifespan_context(app):

            async def controller(request):
                nonlocal armed
                event = json.loads(request.content)
                events.append(event)
                # Acquiring the row lock proves the hook is outside its write transaction.
                async with app.state.database.session() as session:
                    row = await session.scalar(
                        select(ToolRequest)
                        .where(ToolRequest.operation_key == event["operation_id"])
                        .with_for_update(nowait=True)
                    )
                    assert (row.completed_at is None) == (event["name"] == "tools.before_publish")
                interrupt = armed and event["name"] == boundary
                if interrupt:
                    armed = False
                return httpx.Response(
                    200, json={"action": "interrupt" if interrupt else "continue"}
                )

            async with (
                httpx.AsyncClient(
                    base_url="http://controller", transport=httpx.MockTransport(controller)
                ) as observer,
                httpx.AsyncClient(
                    base_url="http://tools", transport=httpx.ASGITransport(app=app)
                ) as client,
            ):
                app.state.hooks = CrashTestHooks("tools", observer)
                seed = (await client.get("/tools/workspace/initial")).json()["workspace_version_id"]
                body = tool_request(seed, path="note.md", content="saved")
                first = await client.post("/tools/execute", json=body)
                assert first.status_code == 503
                assert first.json()["error"]["code"] == "crash_test_interrupted"
                path = f"/tools/operations/{body['operation_id']}/checkpoint"
                saved = (await client.get(path)).json()
                assert (saved is None) == (boundary == "tools.before_publish")
                body["lease_generation"] = 2
                retried = await client.post("/tools/execute", json=body)
                assert retried.status_code == 200, retried.text
                assert len(calls) == (2 if boundary == "tools.before_publish" else 1)
                version = retried.json()["workspace_version_id"]
                assert version != seed
                assert (
                    await client.get(
                        f"/tools/workspace-versions/{version}/file", params={"path": "note.md"}
                    )
                ).content == b"saved"
                if saved:
                    assert (await client.get(path)).json() == saved
                    assert events[-1]["name"] == "tools.result_reused"
                    assert events[-1]["workspace_version_id"] == version
                assert all(e["operation_id"] == body["operation_id"] for e in events)
                assert events[-1]["lease_generation"] == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("action", ["kill", "interrupt", "continue"])
def test_sandbox_hook_targets_one_container_and_cleanup_precedes_publication(
    configured_sandbox, action
):
    async def exercise():
        async with app.router.lifespan_context(app):
            targets = []

            async def controller(request):
                event = json.loads(request.content)
                if event["name"] == "sandbox.started":
                    target = event["sandbox_id"]
                    targets.append(target)
                    if action == "kill":
                        await app.state.runner._checked("kill", target)
                    return httpx.Response(
                        200, json={"action": "interrupt" if action == "interrupt" else "continue"}
                    )
                return httpx.Response(200, json={"action": "continue"})

            async with (
                httpx.AsyncClient(
                    base_url="http://controller", transport=httpx.MockTransport(controller)
                ) as observer,
                httpx.AsyncClient(
                    base_url="http://tools", transport=httpx.ASGITransport(app=app)
                ) as client,
            ):
                app.state.hooks = CrashTestHooks("tools", observer)
                seed = (await client.get("/tools/workspace/initial")).json()["workspace_version_id"]
                command = (
                    "printf saved > note.md; printf output"
                    if action == "continue"
                    else "printf partial > note.md; sleep 30"
                )
                body = tool_request(seed, "run_command", command=command)
                body["sandbox"] = configured_sandbox
                response = await client.post("/tools/execute", json=body)
                assert response.status_code == (200 if action == "continue" else 503), response.text
                assert len(targets) == 1
                assert (await app.state.runner._cli("inspect", targets[0]))[0] != 0
                assert list(app.state.workspace_root.iterdir()) == []
                checkpoint = (
                    await client.get(f"/tools/operations/{body['operation_id']}/checkpoint")
                ).json()
                if action == "continue":
                    assert response.json()["output"]["value"]["stdout"] == "output"
                    assert checkpoint["workspace_version_id"] != seed
                else:
                    assert checkpoint is None

    asyncio.run(exercise())


def test_process_killed_after_commit_reuses_completion(configured, postgres_engine, monkeypatch):
    with TestClient(app) as client:
        seed = client.get("/tools/workspace/initial").json()["workspace_version_id"]
    body = tool_request(seed, path="note.md", content="survives process kill")
    script = """
import asyncio, json, sys, httpx
from durable_agent.contracts import ToolExecuteRequest
from durable_agent.crash_test import CrashTestHooks
from durable_agent.db.session import Database
from durable_agent.tools.execution import execute_operation
async def main():
    db = Database(sys.argv[1])
    async def controller(request):
        event = json.loads(request.content)
        if event['name'] == 'tools.after_commit':
            print(json.dumps(event), flush=True)
            await asyncio.Event().wait()
        return httpx.Response(200, json={'action': 'continue'})
    async with httpx.AsyncClient(base_url='http://controller',
                                transport=httpx.MockTransport(controller)) as client:
        await execute_operation(db, ToolExecuteRequest.model_validate_json(sys.argv[2]),
                                asyncio.Semaphore(1), hooks=CrashTestHooks('tools', client))
asyncio.run(main())
"""

    async def kill_at_commit():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            postgres_engine.url.render_as_string(hide_password=False),
            json.dumps(body),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            line = await asyncio.wait_for(process.stdout.readline(), 5)
            assert line, (await process.stderr.read()).decode()
            event = json.loads(line)
            assert event["name"] == "tools.after_commit"
            assert event["operation_id"] == body["operation_id"]
            process.kill()
            await process.communicate()
            assert process.returncode < 0
            return event["workspace_version_id"]
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()

    version = asyncio.run(kill_at_commit())

    def must_not_execute(*args):
        raise AssertionError("A committed tool call executed again")

    monkeypatch.setattr(execution, "execute_file_tool", must_not_execute)
    with TestClient(app) as client:
        body["lease_generation"] = 2
        response = client.post("/tools/execute", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["workspace_version_id"] == version
        assert (
            client.get(
                f"/tools/workspace-versions/{version}/file", params={"path": "note.md"}
            ).content
            == b"survives process kill"
        )
