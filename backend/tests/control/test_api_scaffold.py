"""Application handlers must remain unavailable before service startup."""

import pytest
from durable_agent.control.app import app
from fastapi.testclient import TestClient

ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def client():
    client = TestClient(app)  # No lifespan: test behavior before startup.
    yield client
    client.close()


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/runs", {"submission_key": ID, "prompt": "Edit a file"}),
        (
            "POST",
            f"/api/conversations/{ID}/runs",
            {"submission_key": ID, "prompt": "Continue", "after_run_id": ID},
        ),
        ("GET", "/api/runs", None),
        ("GET", f"/api/runs/{ID}", None),
        ("GET", f"/api/runs/{ID}/files", None),
        ("GET", f"/api/runs/{ID}/file?version_id={ID}&path=a.md", None),
        ("POST", "/internal/claims", {"worker_id": ID}),
        ("POST", f"/internal/attempts/{ID}/heartbeat", {"worker_id": ID, "lease_generation": 1}),
        (
            "POST",
            f"/internal/attempts/{ID}/report",
            {
                "worker_id": ID,
                "lease_generation": 1,
                "outcome": {
                    "status": "failed",
                    "error": {"code": "timeout", "message": "Timed out", "retryable": True},
                },
            },
        ),
    ],
)
def test_routes_are_unavailable_before_startup(method, path, body, client):
    response = client.request(method, path, json=body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"


def test_submission_rejects_client_configuration_and_invalid_ids(client):
    response = client.post(
        "/api/runs", json={"submission_key": ID, "prompt": "Hi", "model": "other"}
    )
    assert response.status_code == 422
    assert client.get("/api/runs/not-a-uuid").status_code == 422
    assert client.get(f"/api/runs/{ID}/file?path=a.md").status_code == 422


def test_health_does_not_claim_application_readiness(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
