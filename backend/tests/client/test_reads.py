import asyncio
from uuid import uuid4

import httpx
from durable_agent.client import Client

from .test_client import RUN_ID, detail


def test_file_reads_pin_the_listed_version_and_encode_paths():
    version = uuid4()
    requested = []
    path = "notes/one & two # three.md"

    def respond(request):
        requested.append(request)
        if request.url.path.endswith("/files"):
            return httpx.Response(
                200,
                json={
                    "workspace_version_id": str(version),
                    "entries": [{"path": path, "kind": "file", "size_bytes": 3}],
                },
            )
        if request.url.path.endswith("/file"):
            assert dict(request.url.params) == {"version_id": str(version), "path": path}
            return httpx.Response(200, content=b"\x00\xff\x01")
        return httpx.Response(200, json=detail())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                assert await run.files.read(path) == b"\x00\xff\x01"
                listing = await run.files.list(version_id=version)
                assert listing.entries[0].path == path
                assert (
                    await run.files.read(path, version_id=listing.workspace_version_id)
                    == b"\x00\xff\x01"
                )

    asyncio.run(scenario())
    assert [r.url.path.rsplit("/", 1)[-1] for r in requested] == [
        str(RUN_ID),
        "files",
        "file",
        "files",
        "file",
    ]
    assert requested[3].url.params["version_id"] == str(version)


def test_history_children_and_operation_inspection():
    child_id, operation_id = uuid4(), uuid4()
    child = detail("succeeded", id=str(child_id))
    for key in ("final_answer", "workspace_version_id", "operations"):
        child.pop(key)
    child.update(name="research", parent_operation_id=str(operation_id), child_index=0)
    operation = {
        "id": str(operation_id),
        "position": 0,
        "kind": "model",
        "created_at": "2026-10-02T00:00:00Z",
        "completed_at": None,
        "result": None,
        "attempts": [],
    }
    config = {
        "model": "recorded",
        "model_params": {},
        "tool_schemas": [],
        "initial_workspace_version_id": str(uuid4()),
        "sandbox": {"image_digest": "sha256:test", "workspace_max_bytes": 10, "log_max_bytes": 10},
        "max_attempts": 3,
        "max_turns": 5,
        "call_timeout_seconds": 10,
    }

    def respond(request):
        if request.url.path.endswith(f"/operations/{operation_id}"):
            return httpx.Response(
                200,
                json={
                    "operation": {
                        "kind": "model",
                        "request": {"prompt": "Inspect me", "history": []},
                    },
                    "config": config,
                },
            )
        return httpx.Response(200, json=detail(operations=[operation], children=[child]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                assert (await run.history())[0].id == operation_id
                assert (await run.children())[0].id == child_id
                assert (await run.inspect(operation_id)).operation.request.prompt == "Inspect me"

    asyncio.run(scenario())
