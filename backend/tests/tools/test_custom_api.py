from uuid import UUID, uuid4

import pytest
from durable_agent.tools import execution
from durable_agent.tools.app import app
from fastapi.testclient import TestClient

from .test_api import configured as configured
from .test_api import tool_request
from .test_custom_execution import custom_sandbox as custom_sandbox


@pytest.fixture
def configured_custom_sandbox(configured, custom_sandbox, monkeypatch):
    runner, root, request, _ = custom_sandbox
    monkeypatch.setenv("SANDBOX_IMAGE_DIGEST", request.sandbox.image_digest)
    monkeypatch.setenv("SANDBOX_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("SANDBOX_OWNER", runner.owner)
    return request


def test_custom_result_and_files_survive_restart_without_reexecuting(
    configured_custom_sandbox, monkeypatch
):
    request = configured_custom_sandbox
    with TestClient(app) as client:
        request.input_version_id = UUID(
            client.get("/tools/workspace/initial").json()["workspace_version_id"]
        )
        first = client.post("/tools/execute", json=request.model_dump(mode="json"))
        assert first.status_code == 200, first.text
        saved = first.json()
        assert saved["output"]["value"]["attempt_id"] == str(request.attempt_id)
        version = saved["workspace_version_id"]
        assert version != str(request.input_version_id)

    async def must_not_execute(*args, **kwargs):
        raise AssertionError("Completed custom tool must be reused")

    monkeypatch.setattr(execution, "execute_custom_tool", must_not_execute)
    request.attempt_id = uuid4()
    request.lease_generation = 2
    with TestClient(app) as client:
        assert client.post("/tools/execute", json=request.model_dump(mode="json")).json() == saved
        download = client.get(
            f"/tools/workspace-versions/{version}/file", params={"path": "note.txt"}
        )
        assert download.content == b"default"
        for field, value in [("version", "2"), ("entrypoint", "other:save_note")]:
            changed = request.model_copy(
                update={"definition": request.definition.model_copy(update={field: value})}
            )
            assert (
                client.post("/tools/execute", json=changed.model_dump(mode="json")).status_code
                == 409
            )
        changed = request.model_copy(update={"run_id": uuid4()})
        assert (
            client.post("/tools/execute", json=changed.model_dump(mode="json")).status_code == 409
        )


def test_custom_exception_has_no_published_checkpoint(configured_custom_sandbox):
    request = configured_custom_sandbox
    request.arguments = {"mode": "exception"}
    with TestClient(app) as client:
        request.input_version_id = UUID(
            client.get("/tools/workspace/initial").json()["workspace_version_id"]
        )
        response = client.post("/tools/execute", json=request.model_dump(mode="json"))
        assert response.status_code == 400
        assert response.json()["error"]["retryable"] is False
        assert client.get(f"/tools/operations/{request.operation_id}/checkpoint").json() is None


def test_builtin_saved_inputs_remain_compatible_with_added_identity(configured):
    with TestClient(app) as client:
        seed = client.get("/tools/workspace/initial").json()["workspace_version_id"]
        body = tool_request(seed, path="saved", content="before SDK")
        saved = client.post("/tools/execute", json=body)
        assert saved.status_code == 200
        body.update(run_id=str(uuid4()), attempt_id=str(uuid4()))
        assert client.post("/tools/execute", json=body).json() == saved.json()
