from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from .test_inspection_http import claim, report, submit
from .test_worker_http import configured_app, model_result


@pytest.fixture
def client(postgres_engine):
    with TestClient(configured_app(postgres_engine)) as client:
        yield client


def test_only_closed_accepted_rounds_are_checkpoints_even_after_later_failure(client):
    run_id = submit(client)
    path = f"/api/runs/{run_id}"
    assert client.get(path).json()["operations"][0]["checkpoint"] is None
    model, lease = claim(client)
    calls = [
        {"call_id": "write", "name": "write_file", "arguments": {"path": "a", "content": "saved"}},
        {"call_id": "read", "name": "read_file", "arguments": {"path": "missing"}},
    ]
    report(client, model, lease, {"status": "succeeded", "result": model_result("", calls)})
    version = str(uuid4())
    for index, call in enumerate(calls):
        assert all(op["checkpoint"] is None for op in client.get(path).json()["operations"])
        tool, lease = claim(client)
        result = {
            "kind": "tool",
            "call_id": call["call_id"],
            "workspace_version_id": version,
            "output": {"status": "ok", "value": {}}
            if index == 0
            else {"status": "error", "code": "not_found", "message": "Missing"},
        }
        report(client, tool, lease, {"status": "succeeded", "result": result})
    expected = {"workspace_version_id": version}
    operations = client.get(path).json()["operations"]
    assert [op["checkpoint"] for op in operations] == [None, None, expected, None]

    # A later accepted model plan is pending; it does not invalidate the earlier boundary.
    later, lease = claim(client)
    calls[0]["call_id"] = "later"
    report(client, later, lease, {"status": "succeeded", "result": model_result("", calls[:1])})
    assert [op["checkpoint"] for op in client.get(path).json()["operations"]] == [
        None,
        None,
        expected,
        None,
        None,
    ]
    tool, lease = claim(client)
    report(
        client,
        tool,
        lease,
        {
            "status": "failed",
            "error": {
                "code": "sandbox_failed",
                "message": "No accepted result",
                "retryable": False,
            },
        },
    )
    failed = client.get(path).json()
    assert failed["status"] == "failed"
    assert [op["checkpoint"] for op in failed["operations"]] == [None, None, expected, None, None]


@pytest.mark.parametrize("invalid", [None, "incomplete", "arguments", "duplicate"])
def test_model_checkpoint_requires_completed_valid_output(client, invalid):
    run_id = submit(client)
    assignment, lease = claim(client)
    result = model_result()
    if invalid == "incomplete":
        result["output"]["status"] = "incomplete"
    if invalid in ("arguments", "duplicate"):
        call = {"call_id": "a", "name": "read_file", "arguments": {"path": "a"}}
        if invalid == "arguments":
            call["arguments"] = {"path": 5}
        result["output"]["tool_calls"] = [call, call] if invalid == "duplicate" else [call]
    report(client, assignment, lease, {"status": "succeeded", "result": result})
    saved = client.get(f"/api/runs/{run_id}").json()
    assert saved["operations"][0]["checkpoint"] == (
        {"workspace_version_id": assignment["config"]["initial_workspace_version_id"]}
        if invalid is None
        else None
    )
