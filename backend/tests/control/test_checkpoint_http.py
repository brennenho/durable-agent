from uuid import uuid4

import httpx
import pytest
from durable_agent.control.app import create_app
from fastapi.testclient import TestClient

from .test_inspection_http import claim, report, submit
from .test_runtime import settings
from .test_worker_http import model_result


@pytest.mark.parametrize("failure", ["unavailable", "invalid", "disconnected"])
def test_checkpoint_read_scopes_ids_and_keeps_tools_failure_distinct(postgres_engine, failure):
    calls = []

    def tools(request):
        calls.append(request.url.path)
        if request.url.path == "/tools/workspace/initial":
            return httpx.Response(200, json={"workspace_version_id": str(uuid4())})
        if failure == "disconnected":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503 if failure == "unavailable" else 200, json={"bad": "payload"})

    app = create_app(settings(postgres_engine), transport=httpx.MockTransport(tools))
    with TestClient(app) as client:
        run = submit(client)
        model, lease = claim(client)
        path = f"/api/runs/{run}/operations"
        assert client.get(f"{path}/{model['operation_id']}/checkpoint").json() is None
        report(
            client,
            model,
            lease,
            {
                "status": "succeeded",
                "result": model_result(
                    "",
                    [
                        {
                            "call_id": "edit",
                            "name": "write_file",
                            "arguments": {"path": "a", "content": "saved"},
                        }
                    ],
                ),
            },
        )
        tool, _ = claim(client)
        calls.clear()
        for invalid in [
            f"{path}/{uuid4()}",
            f"/api/runs/{uuid4()}/operations/{tool['operation_id']}",
        ]:
            assert client.get(invalid + "/checkpoint").status_code == 404
        assert calls == []
        result = client.get(f"{path}/{tool['operation_id']}/checkpoint")
        assert result.status_code == 503
        assert result.json()["error"]["retryable"] is True
        assert calls == [f"/tools/operations/{tool['operation_id']}/checkpoint"]
        assert client.get(f"/api/runs/{run}").status_code == 200


def test_delegation_checkpoint_is_control_owned(postgres_engine):
    calls = []

    def tools(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"workspace_version_id": str(uuid4())})

    app = create_app(settings(postgres_engine), transport=httpx.MockTransport(tools))
    with TestClient(app) as client:
        run = submit(client)
        model, lease = claim(client)
        report(
            client,
            model,
            lease,
            {
                "status": "succeeded",
                "result": model_result(
                    "",
                    [
                        {
                            "call_id": "delegate",
                            "name": "delegate",
                            "arguments": {"tasks": [{"name": "Review", "prompt": "Review files"}]},
                        }
                    ],
                ),
            },
        )
        operation = client.get(f"/api/runs/{run}").json()["operations"][-1]
        calls.clear()
        response = client.get(f"/api/runs/{run}/operations/{operation['id']}/checkpoint")
        assert response.status_code == 200 and response.json() is None
        assert calls == []
