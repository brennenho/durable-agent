from uuid import uuid4

import pytest
from durable_agent.contracts import ToolRequest
from durable_agent.control.views import tool_summary
from durable_agent.db.models import Operation
from fastapi.testclient import TestClient

from .test_worker_http import configured_app, model_result


@pytest.fixture
def client(postgres_engine):
    with TestClient(configured_app(postgres_engine)) as client:
        yield client


def post(client, path, body):
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def submit(client):
    return post(client, "/api/runs", {"submission_key": str(uuid4()), "prompt": "Edit a file"})[
        "id"
    ]


def claim(client):
    worker = {"worker_id": str(uuid4())}
    assignment = post(client, "/internal/claims", worker)
    return assignment, {**worker, "lease_generation": assignment["lease_generation"]}


def report(client, assignment, lease, outcome):
    post(
        client,
        f"/internal/attempts/{assignment['attempt_id']}/report",
        {**lease, "outcome": outcome},
    )


def test_inspection_reads_saved_context_and_config_and_scopes_both_ids(client):
    first_run = submit(client)
    first, lease = claim(client)
    report(client, first, lease, {"status": "succeeded", "result": model_result()})
    run_id = post(
        client,
        f"/api/conversations/{first_run}/runs",
        {"submission_key": str(uuid4()), "prompt": "Continue", "after_run_id": first_run},
    )["id"]
    model, lease = claim(client)
    client.app.state.runtime.settings.model = "changed-after-submission"
    path = f"/api/runs/{run_id}/operations"
    response = client.get(f"{path}/{model['operation_id']}")
    assert response.status_code == 200
    assert response.json() == {"operation": model["operation"], "config": model["config"]}
    assert model["operation"]["request"]["explicit_input"] == [
        {"role": "user", "content": "Edit a file"},
        *model_result()["native_context"],
        {"role": "user", "content": "Continue"},
    ]

    plan = model_result(
        "Editing",
        [
            {
                "call_id": "edit",
                "name": "write_file",
                "arguments": {"path": "a.py", "content": "x = 1"},
            }
        ],
    )
    report(client, model, lease, {"status": "succeeded", "result": plan})
    tool, lease = claim(client)
    output = {
        "kind": "tool",
        "call_id": "edit",
        "output": {"status": "ok", "value": {}},
        "workspace_version_id": str(uuid4()),
    }
    report(client, tool, lease, {"status": "succeeded", "result": output})
    detail = client.get(f"/api/runs/{run_id}").json()
    pending = detail["operations"][-1]
    response = client.get(f"{path}/{pending['id']}")
    assert response.status_code == 200
    saved = response.json()
    assert saved["config"] == model["config"]
    assert saved["operation"]["request"] == {
        **model["operation"]["request"],
        "explicit_input": [
            *model["operation"]["request"]["explicit_input"],
            *plan["native_context"],
            {
                "type": "function_call_output",
                "call_id": "edit",
                "output": '{"status":"ok","value":{}}',
            },
        ],
    }
    assert all("request" not in op for op in detail["operations"])
    assert pending["tool_name"] is pending["tool_target"] is None
    assert pending["result"] is None
    for invalid in [
        f"{path}/{first['operation_id']}",
        f"{path}/{uuid4()}",
        f"/api/runs/{uuid4()}/operations/{model['operation_id']}",
    ]:
        response = client.get(invalid)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "operation_not_found"


@pytest.mark.parametrize(
    "name,arguments,target",
    [
        ("write_file", {"path": "a" * 180, "content": "private file body"}, "a" * 159 + "…"),
        ("read_file", {"path": "notes.md"}, "notes.md"),
        ("list_files", {}, "."),
        ("run_command", {"command": "  python\n" + "x" * 200}, "python " + "x" * 152 + "…"),
    ],
)
def test_pending_tool_has_bounded_summary_and_complete_input(client, name, arguments, target):
    run_id = submit(client)
    model, lease = claim(client)
    call = {"call_id": "tool", "name": name, "arguments": arguments}
    report(client, model, lease, {"status": "succeeded", "result": model_result("", [call])})
    detail = client.get(f"/api/runs/{run_id}").json()
    tool = detail["operations"][-1]
    assert tool["tool_name"] == name
    assert tool["tool_target"] == target
    assert len(tool["tool_target"]) <= 160
    assert "private file body" not in tool["tool_target"]
    assert tool["attempts"] == []
    response = client.get(f"/api/runs/{run_id}/operations/{tool['id']}")
    assert response.status_code == 200
    assert response.json() == {
        "operation": {
            "kind": "tool",
            "request": ToolRequest(
                call=call, input_version_id=model["config"]["initial_workspace_version_id"]
            ).model_dump(mode="json"),
        },
        "config": model["config"],
    }


def test_attempt_details_preserve_retry_error_provider_ids_and_usage(client):
    run_id = submit(client)
    first, lease = claim(client)
    error = {
        "code": "overloaded",
        "message": "Try again",
        "retryable": True,
        "retry_after_seconds": 0,
    }
    report(
        client, first, lease, {"status": "failed", "error": error, "provider_request_id": "req-1"}
    )
    retrying = client.get(f"/api/runs/{run_id}").json()
    assert retrying["status"] == "retrying"
    assert retrying["next_attempt_at"] is not None
    assert retrying["max_attempts"] == first["config"]["max_attempts"]
    first_view = retrying["operations"][0]["attempts"][0]
    assert first_view["error"] == error
    assert first_view["provider_request_id"] == "req-1"
    assert first_view["usage"] is None
    second, lease = claim(client)
    assert second["operation_id"] == first["operation_id"]
    usage = {
        "input_tokens": 12,
        "output_tokens": 4,
        "output_tokens_details": {"reasoning_tokens": 2},
    }
    report(
        client,
        second,
        lease,
        {
            "status": "succeeded",
            "result": model_result(),
            "provider_request_id": "req-2",
            "usage": usage,
        },
    )
    attempts = client.get(f"/api/runs/{run_id}").json()["operations"][0]["attempts"]
    assert attempts[0] == first_view
    assert attempts[1]["status"] == "accepted"
    assert attempts[1]["provider_request_id"] == "req-2"
    assert attempts[1]["usage"] == usage
    assert attempts[1]["error"] is None


@pytest.mark.parametrize("arguments", [{}, {"command": 42}, {"command": None}])
def test_invalid_display_target_is_omitted(arguments):
    operation = Operation(
        kind="tool",
        request={
            "call": {"call_id": "tool", "name": "run_command", "arguments": arguments},
            "input_version_id": str(uuid4()),
        },
    )
    assert tool_summary(operation) == {"tool_name": "run_command", "tool_target": None}
