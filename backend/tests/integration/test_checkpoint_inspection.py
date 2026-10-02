"""Tools publication is observable before Control accepts, without exposing files."""

import asyncio
from contextlib import AsyncExitStack
from uuid import uuid4

import httpx
from durable_agent.control.app import create_app
from durable_agent.control.runtime import Settings
from durable_agent.tools.app import app as tools_app


def test_checkpoint_is_saved_before_acceptance_and_survives_tools_restart(
    postgres_engine, monkeypatch
):
    url = postgres_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    for name in ("WORKSPACE_INPUT_DIR", "SANDBOX_WORKSPACE_ROOT", "SANDBOX_IMAGE_DIGEST"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        database_url=url,
        tools_url="http://tools",
        model="fixture",
        image_digest="sha256:" + "a" * 64,
    )
    transport = httpx.ASGITransport(app=tools_app)
    control = create_app(settings, transport=transport)

    async def exercise():
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(tools_app.router.lifespan_context(tools_app))
            await stack.enter_async_context(control.router.lifespan_context(control))
            tools = await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://tools")
            )
            api = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=control), base_url="http://control"
                )
            )
            worker = {"worker_id": str(uuid4())}

            async def post(path, body):
                result = await api.post(path, json=body)
                assert result.status_code == 200, result.text
                return result.json()

            run = await post(
                "/api/runs", {"submission_key": str(uuid4()), "prompt": "Write a note"}
            )
            assignment = await post("/internal/claims", worker)
            call = {
                "call_id": "edit",
                "name": "write_file",
                "arguments": {"path": "note.md", "content": "saved"},
            }
            await post(
                f"/internal/attempts/{assignment['attempt_id']}/report",
                {
                    **worker,
                    "lease_generation": assignment["lease_generation"],
                    "outcome": {
                        "status": "succeeded",
                        "result": {
                            "kind": "model",
                            "output": {"status": "completed", "text": "", "tool_calls": [call]},
                            "native_context": [],
                        },
                    },
                },
            )
            assignment = await post("/internal/claims", worker)
            operation = assignment["operation_id"]
            path = f"/api/runs/{run['id']}/operations/{operation}/checkpoint"
            assert (await api.get(path)).json() is None
            request = {
                "operation_id": operation,
                "input_version_id": assignment["operation"]["request"]["input_version_id"],
                "name": call["name"],
                "arguments": call["arguments"],
                "lease_generation": assignment["lease_generation"],
                "deadline_at": assignment["deadline_at"],
                "sandbox": assignment["config"]["sandbox"],
            }
            completed = await tools.post("/tools/execute", json=request)
            assert completed.status_code == 200, completed.text
            saved = (await api.get(path)).json()
            version = completed.json()["workspace_version_id"]
            assert saved == {"completed_at": saved["completed_at"], "workspace_version_id": version}
            assert saved["completed_at"] is not None
            pending = (await api.get(f"/api/runs/{run['id']}")).json()
            assert pending["operations"][-1]["result"] is None
            assert pending["workspace_version_id"] != version
            assert (
                await api.get(
                    f"/api/runs/{run['id']}/file", params={"version_id": version, "path": "note.md"}
                )
            ).status_code == 404
            await post(
                f"/internal/attempts/{assignment['attempt_id']}/report",
                {
                    **worker,
                    "lease_generation": assignment["lease_generation"],
                    "outcome": {
                        "status": "succeeded",
                        "result": {"kind": "tool", "call_id": "edit", **completed.json()},
                    },
                },
            )
            assert (await api.get(path)).json() == saved
            accepted = (await api.get(f"/api/runs/{run['id']}")).json()
            assert accepted["workspace_version_id"] == version
            assert (
                await api.get(
                    f"/api/runs/{run['id']}/file", params={"version_id": version, "path": "note.md"}
                )
            ).content == b"saved"
        async with tools_app.router.lifespan_context(tools_app):
            async with httpx.AsyncClient(transport=transport, base_url="http://tools") as tools:
                assert (
                    await tools.get(f"/tools/operations/{operation}/checkpoint")
                ).json() == saved

    asyncio.run(exercise())
