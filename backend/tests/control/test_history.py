from datetime import UTC, datetime
from uuid import uuid4

from durable_agent.contracts import ModelRequest, ModelResult, PreviousTurn, ToolResult
from durable_agent.control.history import restore_state, workspace_version
from durable_agent.db.models import Operation, Run


def model(*calls):
    return ModelResult(
        kind="model",
        output={"status": "completed", "text": "Done", "tool_calls": list(calls)},
        native_context=[{"opaque": "saved"}],
    )


def test_history_restores_pending_tools_and_accepted_workspace(config):
    first = {"call_id": "a", "name": "read_file", "arguments": {"path": "a"}}
    second = {**first, "call_id": "b"}
    edited_version = uuid4()
    run = Run(config=config.model_dump(mode="json"), prompt="Hi")

    def saved(result):
        return Operation(
            request={"prompt": "Hi", "history": []},
            result=result.model_dump(mode="json"),
            completed_at=datetime.now(UTC),
        )

    operations = [saved(model(first, second))]
    state = restore_state(run, operations)
    assert [call.call_id for call in state.pending_tool_calls] == ["a", "b"]
    assert workspace_version(state) == config.initial_workspace_version_id
    operations.append(
        saved(
            ToolResult(
                kind="tool",
                call_id="a",
                output={"status": "error", "code": "not_found", "message": "Missing"},
                workspace_version_id=edited_version,
            )
        )
    )
    operations.append(Operation(result=None, completed_at=None))
    state = restore_state(run, operations)
    assert state.turn_count == 1
    assert state.pending_tool_calls[0].call_id == "b"
    assert workspace_version(state) == edited_version
    assert state.history[0].native_context == [{"opaque": "saved"}]


def test_restoration_preserves_prior_turns_but_resets_execution_state(config):
    previous = PreviousTurn(
        prompt="Earlier request",
        history=[model({"call_id": "unfinished", "name": "read_file", "arguments": {"path": "a"}})],
        terminal_error={"code": "interrupted", "message": "No report", "retryable": False},
    )
    run = Run(config=config.model_dump(mode="json"), prompt="Continue")
    request = ModelRequest(prompt=run.prompt, history=[], previous_turns=[previous])
    operation = Operation(position=0, kind="model", request=request.model_dump(mode="json"))
    restored = restore_state(run, [operation])
    assert restored.previous_turns == [previous]
    assert restored.history == restored.pending_tool_calls == []
    assert restored.turn_count == 0
    assert workspace_version(restored) == config.initial_workspace_version_id
