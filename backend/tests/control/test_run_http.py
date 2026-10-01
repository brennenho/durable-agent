from uuid import uuid4

import httpx
from durable_agent.control.app import create_app
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from .test_runtime import settings


def test_run_http_submissions_recover_lost_ack_and_return_saved_views(postgres_engine):
    seed, calls = uuid4(), []

    def tools(request):
        calls.append(request.url.path)
        if len(calls) > 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"workspace_version_id": str(seed)})

    app = create_app(settings(postgres_engine), transport=httpx.MockTransport(tools))
    with TestClient(app) as client:
        body = {"submission_key": str(uuid4()), "prompt": "Edit a file"}
        created = client.post("/api/runs", json=body)
        assert created.status_code == 200
        run_id = created.json()["id"]
        assert client.post("/api/runs", json=body).json() == created.json()
        assert len(calls) == 1
        assert client.post("/api/runs", json={**body, "prompt": "Changed"}).status_code == 409
        saved = client.get(f"/api/runs/{run_id}").json()
        assert saved["status"] == "queued"
        assert saved["workspace_version_id"] == str(seed)
        assert saved["operations"][0]["attempts"] == []
        assert saved["operations"][0]["result"] is None
        assert client.get("/api/runs").json() == [created.json()]
        assert client.get(f"/api/runs/{uuid4()}").status_code == 404
        failed = client.post("/api/runs", json={**body, "submission_key": str(uuid4())})
        assert failed.status_code == 503
        assert len(client.get("/api/runs").json()) == 1


def test_database_outage_returns_unavailable_not_empty_success(postgres_engine, monkeypatch):
    app = create_app(settings(postgres_engine))
    with TestClient(app) as client:

        async def unavailable(session):
            raise OperationalError("query", {}, OSError("database unavailable"))

        monkeypatch.setattr("durable_agent.control.app.list_summaries", unavailable)
        response = client.get("/api/runs")
        assert response.status_code == 503
        assert response.json()["error"]["retryable"] is True
