"""Tools replaces the stub assertions as it implements each route."""

import pytest
from durable_agent.tools.app import app
from fastapi.testclient import TestClient

ID = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize(
    "method,path,body",
    [
        (
            "POST",
            "/tools/execute",
            {
                "operation_id": ID,
                "input_version_id": ID,
                "name": "read_file",
                "arguments": {"path": "a.md"},
                "lease_generation": 1,
                "deadline_at": "2026-10-01T20:02:00Z",
                "sandbox": {
                    "image_digest": "sha256:test",
                    "workspace_max_bytes": 1024,
                    "log_max_bytes": 1024,
                },
            },
        ),
        ("GET", "/tools/workspace/initial", None),
        ("GET", f"/tools/workspace-versions/{ID}/files", None),
        ("GET", f"/tools/workspace-versions/{ID}/file?path=a.md", None),
    ],
)
def test_routes_report_unimplemented_instead_of_success(method, path, body):
    with TestClient(app) as client:
        response = client.request(method, path, json=body)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


def test_health_does_not_claim_application_readiness():
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
