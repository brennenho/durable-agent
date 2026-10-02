import asyncio
from uuid import uuid4

import httpx
import pytest
from durable_agent.contracts import ModelResult, ToolResult
from durable_agent.control.app import create_app
from fastapi.testclient import TestClient

from .test_leases import claimed, queued
from .test_reports import report, report_body
from .test_runtime import settings


def saved_edit(database, config):
    async def prepare():
        async with database() as db:
            run_id = await queued(db, config)
            assignment = await claimed(db)
            model = ModelResult(
                kind="model",
                output={
                    "status": "completed",
                    "text": "",
                    "tool_calls": [
                        {
                            "call_id": call_id,
                            "name": "write_file",
                            "arguments": {"path": "a.md", "content": "saved"},
                        }
                        for call_id in ["first", "second"]
                    ],
                },
                native_context=[],
            )
            await report(db, assignment, await report_body(db, assignment, model))
            versions = []
            for call_id in ["first", "second"]:
                assignment = await claimed(db)
                result = ToolResult(
                    kind="tool",
                    call_id=call_id,
                    output={"status": "ok", "value": {"bytes_written": 5}},
                    workspace_version_id=uuid4(),
                )
                await report(db, assignment, await report_body(db, assignment, result))
                versions.append(result.workspace_version_id)
            assignment = await claimed(db)
            await report(db, assignment, await report_body(db, assignment))
            return run_id, versions

    return asyncio.run(prepare())


def test_saved_files_use_accepted_version_and_allow_seed_download(
    postgres_engine, database, config
):
    run_id, versions = saved_edit(database, config)
    _, foreign_versions = saved_edit(database, config)
    version = versions[-1]
    calls = []
    path, content = 'notes/a "résumé".md', b"saved\x00\xff"
    entries = [{"path": path, "kind": "file", "size_bytes": len(content)}]

    def tools(request):
        calls.append(request.url)
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"entries": entries})
        assert request.url.params["path"] == path
        return httpx.Response(200, content=content)

    app = create_app(settings(postgres_engine), transport=httpx.MockTransport(tools))
    with TestClient(app) as client:
        listing = client.get(f"/api/runs/{run_id}/files")
        assert listing.json() == {"workspace_version_id": str(version), "entries": entries}
        assert str(version) in calls[-1].path
        for allowed in [config.initial_workspace_version_id, *versions]:
            historical = client.get(
                f"/api/runs/{run_id}/files", params={"version_id": str(allowed)}
            )
            assert historical.status_code == 200
            assert historical.json() == {"workspace_version_id": str(allowed), "entries": entries}
            assert str(allowed) in calls[-1].path
            response = client.get(
                f"/api/runs/{run_id}/file", params={"version_id": str(allowed), "path": path}
            )
            assert response.content == content
            assert response.headers["content-type"] == "application/octet-stream"
            assert response.headers["content-disposition"] == (
                "attachment; filename*=UTF-8''a%20%22r%C3%A9sum%C3%A9%22.md"
            )
            assert str(allowed) in calls[-1].path
        before = len(calls)
        for unknown_run, unknown_version in [
            (run_id, foreign_versions[-1]),
            (run_id, uuid4()),
            (uuid4(), version),
        ]:
            response = client.get(
                f"/api/runs/{unknown_run}/file",
                params={"version_id": str(unknown_version), "path": path},
            )
            assert response.status_code == 404
            response = client.get(
                f"/api/runs/{unknown_run}/files", params={"version_id": str(unknown_version)}
            )
            assert response.status_code == 404
        assert client.get(f"/api/runs/{uuid4()}/files").status_code == 404
        assert client.get(f"/api/runs/{run_id}/files?version_id=invalid").status_code == 422
        assert len(calls) == before


@pytest.mark.parametrize(
    "status,content,expected",
    [(404, b"{}", 404), (503, b"{}", 503), (200, b"{}", 503), (200, b"not JSON", 503)],
)
def test_invalid_or_unavailable_listing_is_an_error(
    postgres_engine, database, config, status, content, expected
):
    run_id, _ = saved_edit(database, config)
    app = create_app(
        settings(postgres_engine),
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=content)),
    )
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run_id}/files")
        assert response.status_code == expected
        assert "error" in response.json()


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
def test_file_reads_return_unavailable_on_transport_failure(
    postgres_engine, database, config, failure
):
    run_id, versions = saved_edit(database, config)

    def unavailable(request):
        raise failure("Tool service unavailable", request=request)

    app = create_app(settings(postgres_engine), transport=httpx.MockTransport(unavailable))
    with TestClient(app) as client:
        for response in [
            client.get(f"/api/runs/{run_id}/files"),
            client.get(
                f"/api/runs/{run_id}/file", params={"version_id": str(versions[-1]), "path": "a.md"}
            ),
        ]:
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "tools_unavailable"
            assert response.json()["error"]["retryable"] is True


def test_run_without_results_lists_seed_and_missing_file_returns_404(
    postgres_engine, database, config
):
    async def prepare():
        async with database() as db:
            return await queued(db, config)

    run_id = asyncio.run(prepare())

    def tools(request):
        assert str(config.initial_workspace_version_id) in request.url.path
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"entries": []})
        return httpx.Response(404)

    app = create_app(settings(postgres_engine), transport=httpx.MockTransport(tools))
    with TestClient(app) as client:
        assert client.get(f"/api/runs/{run_id}/files").json() == {
            "workspace_version_id": str(config.initial_workspace_version_id),
            "entries": [],
        }
        response = client.get(
            f"/api/runs/{run_id}/file",
            params={"version_id": str(config.initial_workspace_version_id), "path": "missing"},
        )
        assert response.status_code == 404
