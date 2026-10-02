import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from durable_agent.crash_test import CrashTestHooks
from durable_agent.db.models import ToolRequest
from durable_agent.tools.app import app
from sqlalchemy import select

from .test_custom_api import configured as configured
from .test_custom_api import configured_custom_sandbox as configured_custom_sandbox
from .test_custom_api import custom_sandbox as custom_sandbox


@pytest.mark.parametrize(
    "boundary", ["sandbox.started", "tools.before_publish", "tools.after_commit"]
)
def test_custom_retry_reuses_only_committed_work(configured, configured_custom_sandbox, boundary):
    request = configured_custom_sandbox
    (configured / "note.txt").write_text("start|")
    request.arguments = {"mode": "append", "content": "once"}
    original_attempt = request.attempt_id

    async def exercise():
        events = []
        armed = True
        async with app.router.lifespan_context(app):

            async def controller(call):
                nonlocal armed
                event = json.loads(call.content)
                events.append(event)
                async with app.state.database.session() as session:
                    row = await session.scalar(
                        select(ToolRequest)
                        .where(ToolRequest.operation_key == request.operation_id)
                        .with_for_update(nowait=True)
                    )
                    if armed:
                        assert (row.completed_at is not None) == (
                            event["name"] == "tools.after_commit"
                        )
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
                request.input_version_id = UUID(seed)
                first = await client.post("/tools/execute", json=request.model_dump(mode="json"))
                assert first.status_code == 503, first.text
                checkpoint = f"/tools/operations/{request.operation_id}/checkpoint"
                saved = (await client.get(checkpoint)).json()
                committed = boundary == "tools.after_commit"
                assert (saved is not None) == committed
                request.attempt_id = uuid4()
                request.lease_generation = 2
                retried = await client.post("/tools/execute", json=request.model_dump(mode="json"))
                assert retried.status_code == 200, retried.text
                result = retried.json()
                accepted_attempt = original_attempt if committed else request.attempt_id
                assert result["output"]["value"]["attempt_id"] == str(accepted_attempt)
                version = result["workspace_version_id"]
                content = await client.get(
                    f"/tools/workspace-versions/{version}/file", params={"path": "note.txt"}
                )
                assert content.content == b"start|once"
                starts = [event for event in events if event["name"] == "sandbox.started"]
                assert len(starts) == (1 if committed else 2)
                assert len({event["sandbox_id"] for event in starts}) == len(starts)
                assert all(event["operation_id"] == str(request.operation_id) for event in events)
                if committed:
                    assert events[-1]["name"] == "tools.result_reused"
                    assert (await client.get(checkpoint)).json() == saved

    asyncio.run(exercise())


def test_new_custom_attempt_fences_a_late_result(configured_custom_sandbox):
    request = configured_custom_sandbox

    async def exercise():
        held, release = asyncio.Event(), asyncio.Event()
        async with app.router.lifespan_context(app):

            async def controller(call):
                event = json.loads(call.content)
                if event["name"] == "tools.before_publish" and event["lease_generation"] == 1:
                    held.set()
                    await release.wait()
                return httpx.Response(200, json={"action": "continue"})

            async with (
                httpx.AsyncClient(
                    base_url="http://controller", transport=httpx.MockTransport(controller)
                ) as observer,
                httpx.AsyncClient(
                    base_url="http://tools", transport=httpx.ASGITransport(app=app)
                ) as client,
                asyncio.timeout(30),
            ):
                app.state.hooks = CrashTestHooks("tools", observer)
                request.input_version_id = UUID(
                    (await client.get("/tools/workspace/initial")).json()["workspace_version_id"]
                )
                old = asyncio.create_task(
                    client.post("/tools/execute", json=request.model_dump(mode="json"))
                )
                try:
                    await held.wait()
                    request.attempt_id = uuid4()
                    request.lease_generation = 2
                    current = await client.post(
                        "/tools/execute", json=request.model_dump(mode="json")
                    )
                    assert current.status_code == 200, current.text
                    assert current.json()["output"]["value"]["attempt_id"] == str(
                        request.attempt_id
                    )
                    release.set()
                    rejected = await old
                    assert rejected.status_code == 409, rejected.text
                    replay = await client.post(
                        "/tools/execute", json=request.model_dump(mode="json")
                    )
                    assert replay.json() == current.json()
                finally:
                    release.set()
                    await asyncio.gather(old, return_exceptions=True)

    asyncio.run(exercise())
