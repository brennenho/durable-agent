import asyncio
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from durable_agent.db.models import ToolRequest, WorkspaceVersion
from durable_agent.tools import execution
from durable_agent.tools.app import app
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


@pytest.fixture
def configured(postgres_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres_engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("WORKSPACE_INPUT_DIR", str(tmp_path))
    (tmp_path / "seed.md").write_text("seed")
    return tmp_path


def tool_request(version, name="write_file", **arguments):
    return {
        "operation_id": str(uuid4()),
        "input_version_id": version,
        "name": name,
        "arguments": arguments,
        "lease_generation": 1,
        "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "sandbox": {
            "image_digest": "sha256:test",
            "workspace_max_bytes": 1024,
            "log_max_bytes": 1024,
        },
    }


def test_write_replay_and_saved_reads_survive_service_restart(configured):
    with TestClient(app) as client:
        seed = client.get("/tools/workspace/initial").json()["workspace_version_id"]
        body = tool_request(seed, path="dir/a.md", content="saved")
        first = client.post("/tools/execute", json=body)
        assert first.status_code == 200
        assert first.json()["output"]["value"] == {"bytes_written": 5}
        assert client.post("/tools/execute", json=body).json() == first.json()
        version = first.json()["workspace_version_id"]
        assert version != seed
        changed = {**body, "arguments": {"path": "other", "content": "changed"}}
        assert client.post("/tools/execute", json=changed).status_code == 409
    (configured / "seed.md").write_text("changed source")
    with TestClient(app) as client:
        assert client.post("/tools/execute", json=body).json() == first.json()
        listing = client.get(f"/tools/workspace-versions/{version}/files")
        assert [entry["path"] for entry in listing.json()["entries"]] == [
            "dir",
            "dir/a.md",
            "seed.md",
        ]
        download = client.get(
            f"/tools/workspace-versions/{version}/file", params={"path": "dir/a.md"}
        )
        assert download.content == b"saved"
        assert "attachment" in download.headers["content-disposition"]
        assert (
            client.get(f"/tools/workspace-versions/{seed}/file", params={"path": "seed.md"}).content
            == b"seed"
        )
        assert (
            client.get(
                f"/tools/workspace-versions/{version}/file", params={"path": "missing"}
            ).status_code
            == 404
        )
        assert client.get(f"/tools/workspace-versions/{uuid4()}/files").status_code == 404


def test_read_business_error_and_deadline_have_distinct_outcomes(configured):
    with TestClient(app) as client:
        seed = client.get("/tools/workspace/initial").json()["workspace_version_id"]
        body = tool_request(seed, "read_file", path="missing")
        response = client.post("/tools/execute", json=body)
        assert response.status_code == 200
        assert response.json()["output"]["code"] == "not_found"
        assert response.json()["workspace_version_id"] == seed
        body = tool_request(seed, "read_file", path="seed.md")
        body["deadline_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        response = client.post("/tools/execute", json=body)
        assert response.status_code == 504
        assert response.json()["error"]["retryable"] is True
        assert (
            client.post("/tools/execute", json=tool_request(seed, "read_file")).status_code == 400
        )
        assert (
            client.post(
                "/tools/execute", json=tool_request(seed, "run_command", command="true")
            ).status_code
            == 501
        )
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503


def test_bad_seed_configuration_fails_startup(configured, monkeypatch):
    monkeypatch.setenv("WORKSPACE_INPUT_DIR", str(configured / "missing"))
    with pytest.raises(FileNotFoundError), TestClient(app):
        pass


def test_timeout_cannot_publish_a_late_thread_result(configured, postgres_engine, monkeypatch):
    release, finished = Event(), Event()
    started = []
    real_execute = execution.execute_file_tool

    def slow_execute(*args):
        started.append(args)
        release.wait(5)
        try:
            return real_execute(*args)
        finally:
            finished.set()

    monkeypatch.setattr(execution, "execute_file_tool", slow_execute)
    with TestClient(app) as client:
        app.state.slots = asyncio.Semaphore(1)
        seed = client.get("/tools/workspace/initial").json()["workspace_version_id"]
        body = tool_request(seed, path="late", content="discard")
        body["deadline_at"] = (datetime.now(UTC) + timedelta(seconds=0.1)).isoformat()
        try:
            assert client.post("/tools/execute", json=body).status_code == 504
            queued = tool_request(seed, path="queued", content="discard")
            queued["deadline_at"] = (datetime.now(UTC) + timedelta(seconds=0.1)).isoformat()
            assert client.post("/tools/execute", json=queued).status_code == 504
            assert len(started) == 1
        finally:
            release.set()
        assert finished.wait(5)
        with Session(postgres_engine) as session:
            row = session.get(ToolRequest, body["operation_id"])
            assert row.result is None
            assert session.scalar(select(func.count()).select_from(WorkspaceVersion)) == 1
