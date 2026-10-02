from uuid import uuid4

import pytest
from durable_agent.contracts import ModelResult, ToolResult
from durable_agent.harness.sdk_results import to_step_result


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_model_callback_receives_text_plan_and_native_context(status):
    operation_id, workspace = uuid4(), uuid4()
    native = [{"type": "reasoning", "encrypted_content": "opaque"}]
    call = {"call_id": "read", "name": "read_file", "arguments": {"path": "a.md"}}
    result = ModelResult(
        kind="model",
        output={"status": status, "text": "Inspect the file", "tool_calls": [call]},
        native_context=native,
    )
    original = result.model_dump()

    callback_result = to_step_result(operation_id, result, workspace)

    assert callback_result.operation_id == operation_id
    assert callback_result.kind == "model"
    assert callback_result.workspace_version_id == workspace
    assert callback_result.error is None
    assert callback_result.value == {
        "status": status,
        "text": "Inspect the file",
        "tool_calls": [call],
        "native_context": native,
    }
    # Callback-owned JSON cannot mutate the accepted provider response.
    callback_result.value["native_context"][0]["encrypted_content"] = "changed"
    callback_result.value["tool_calls"][0]["arguments"]["path"] = "changed.md"
    assert result.model_dump() == original


def test_tool_callback_receives_value_and_the_saved_workspace():
    saved = uuid4()
    result = ToolResult(
        kind="tool",
        call_id="edit",
        output={"status": "ok", "value": {"files": ["a.md"], "exit_code": 1}},
        workspace_version_id=saved,
    )

    callback_result = to_step_result(uuid4(), result, uuid4(), tool_name="run_command")

    assert callback_result.kind == "tool"
    assert callback_result.value == {"files": ["a.md"], "exit_code": 1}
    assert callback_result.error is None  # A failing test is still a completed command.
    assert callback_result.workspace_version_id == saved
    callback_result.value["files"].append("other.md")
    assert result.output.value["files"] == ["a.md"]


def test_business_error_is_available_to_the_callback_without_a_fake_value():
    workspace = uuid4()
    result = ToolResult(
        kind="tool",
        call_id="read",
        output={"status": "error", "code": "not_found", "message": "File is missing"},
        workspace_version_id=workspace,
    )

    callback_result = to_step_result(uuid4(), result, workspace, tool_name="read_file")

    assert callback_result.kind == "tool"
    assert callback_result.value is None
    assert callback_result.error.code == "not_found"
    assert callback_result.error.message == "File is missing"
    assert callback_result.workspace_version_id == workspace


def test_child_batch_preserves_structured_outputs_errors_and_parent_workspace():
    workspace = uuid4()
    children = [
        {"run_id": str(uuid4()), "name": "writer", "status": "succeeded", "output": [1, 2]},
        {
            "run_id": str(uuid4()),
            "name": "reviewer",
            "status": "failed",
            "output": None,
            "error": {"code": "limit", "message": "Step limit reached"},
        },
    ]
    result = ToolResult(
        kind="tool",
        call_id="delegate",
        output={"status": "ok", "value": {"children": children}},
        workspace_version_id=workspace,
    )

    callback_result = to_step_result(uuid4(), result, workspace, tool_name="delegate")

    assert callback_result.kind == "children"
    assert callback_result.value == {"children": children}
    assert callback_result.error is None
    assert callback_result.workspace_version_id == workspace
