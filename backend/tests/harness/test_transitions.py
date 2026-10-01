from uuid import UUID

import pytest
from durable_agent.contracts import AgentConfig, ModelResult, PreviousTurn, ToolCall, ToolResult
from durable_agent.harness import advance, start
from durable_agent.harness.contracts import AgentState

VERSION = UUID("00000000-0000-4000-8000-000000000001")


@pytest.fixture
def state():
    return AgentState(
        config=AgentConfig(
            model="test",
            model_params={},
            tool_schemas=[
                {"name": name} for name in ("write_file", "read_file", "list_files", "run_command")
            ],
            initial_workspace_version_id=VERSION,
            sandbox={
                "image_digest": "sha256:test",
                "workspace_max_bytes": 1024,
                "log_max_bytes": 1024,
            },
            max_attempts=5,
            max_turns=2,
            call_timeout_seconds=120,
        ),
        prompt="Edit a file",
        history=[],
        pending_tool_calls=[],
        turn_count=0,
    )


def model(*calls, status="completed", text="Done"):
    return ModelResult(
        kind="model",
        output={"status": status, "text": text, "tool_calls": list(calls)},
        native_context=[{"type": "reasoning", "encrypted_content": "opaque"}],
    )


def call(call_id="write", name="write_file", arguments=None):
    return ToolCall(
        call_id=call_id,
        name=name,
        arguments={"path": "a.md", "content": "Hi"} if arguments is None else arguments,
    )


def tool(call_id="write", error=False):
    output = (
        {"status": "error", "code": "not_found", "message": "Missing"}
        if error
        else {"status": "ok", "value": {"bytes_written": 2}}
    )
    return ToolResult(kind="tool", call_id=call_id, output=output, workspace_version_id=VERSION)


def test_start_and_final_answer_are_pure(state):
    before = state.model_dump()
    first = start(state.config, state.prompt)
    assert first.next.kind == "model"
    assert first.next.request.prompt == state.prompt
    assert first.next.request.history == []
    assert advance(state, model()).final_answer == "Done"
    assert state.model_dump() == before


def test_prior_turns_reach_every_model_call_without_using_the_current_turn_budget(state):
    previous = PreviousTurn(
        prompt="Earlier request",
        history=[model(call("unfinished"))],
        terminal_error={"code": "timeout", "message": "Interrupted", "retryable": True},
    )
    original = previous.model_dump()
    state.previous_turns = [previous]
    first = start(state.config, state.prompt, previous_turns=state.previous_turns)
    assert first.next.request.previous_turns == [previous]
    assert first.next.request.history == []
    planned = model(call())
    assert advance(state, planned).next.call == call()
    state.history, state.pending_tool_calls, state.turn_count = [planned], [call()], 1
    next_step = advance(state, tool())
    assert next_step.next.request.previous_turns == [previous]
    assert next_step.next.request.history == [planned, tool()]
    assert previous.model_dump() == original


def test_existing_states_and_requests_default_to_no_prior_turns(state):
    payload = state.model_dump()
    payload.pop("previous_turns")
    assert AgentState.model_validate(payload).previous_turns == []
    assert start(state.config, state.prompt).next.request.previous_turns == []


def test_reused_call_id_from_an_earlier_turn_rejects_the_entire_new_plan(state):
    state.previous_turns = [
        PreviousTurn(
            prompt="Earlier request",
            history=[model(call()), tool(), model()],
        )
    ]
    transition = advance(state, model(call("fresh"), call()))
    assert transition.status == "failed"
    assert transition.error.code == "invalid_tool_call"


def test_two_tools_execute_in_order_then_return_full_context_to_model(state):
    first, second = call(), call("read", "read_file", {"path": "a.md"})
    planned = model(first, second)
    assert advance(state, planned).next.call == first
    state.history = [planned]
    state.turn_count = 1
    state.pending_tool_calls = [first, second]
    assert advance(state, tool()).next.call == second
    state.history.append(tool())
    state.pending_tool_calls = [second]
    result = tool("read", error=True)
    before = state.model_dump()
    resumed = advance(state, result)
    assert resumed.next.kind == "model"
    assert resumed.next.request.history == [planned, tool(), result]
    assert resumed.next.request.history[0].native_context == planned.native_context
    assert state.model_dump() == before


@pytest.mark.parametrize(
    "bad",
    [
        call(name="unknown"),
        call(call_id=""),
        call(arguments={"path": "a.md"}),
        call(arguments={"path": 5, "content": "Hi"}),
        call(arguments={"path": "a.md", "content": "Hi", "extra": True}),
    ],
)
def test_invalid_later_call_rejects_entire_plan(state, bad):
    transition = advance(state, model(call("first"), bad))
    assert transition.status == "failed"
    assert transition.error.code == "invalid_tool_call"
    assert transition.error.retryable is False


def test_disabled_and_duplicate_tool_calls_are_rejected(state):
    assert advance(state, model(call(), call())).error.code == "invalid_tool_call"
    state.history = [model(call())]
    assert advance(state, model(call())).error.code == "invalid_tool_call"
    state.history = []
    state.config.tool_schemas = []
    assert advance(state, model(call())).error.code == "invalid_tool_call"


@pytest.mark.parametrize(
    "name,arguments",
    [("list_files", {}), ("read_file", {"path": "a"}), ("run_command", {"command": "pytest"})],
)
def test_supported_arguments_preserve_original_defaults(state, name, arguments):
    planned = call(name=name, arguments=arguments)
    assert advance(state, model(planned)).next.call == planned


def test_incomplete_output_cannot_dispatch_tools_or_finish(state):
    for result in (model(status="incomplete"), model(call(), status="incomplete")):
        assert advance(state, result).error.code == "incomplete_model_output"


def test_unexpected_result_order_fails(state):
    assert advance(state, tool()).error.code == "invalid_result"
    state.pending_tool_calls = [call()]
    assert advance(state, tool("other")).error.code == "invalid_result"
    assert advance(state, model()).error.code == "invalid_result"


def test_last_model_turn_may_finish_or_run_tools_but_cannot_schedule_another_model(state):
    state.turn_count = 1
    assert advance(state, model()).status == "finished"
    assert advance(state, model(call())).next.kind == "tool"
    state.turn_count = 2
    assert advance(state, model()).error.code == "turn_limit"
    state.pending_tool_calls = [call(), call("second")]
    assert advance(state, tool()).next.call.call_id == "second"
    state.pending_tool_calls = [call("second")]
    assert advance(state, tool("second")).error.code == "turn_limit"


@pytest.mark.parametrize(
    "schemas", [[{}], [{"name": []}], [{"name": "unknown"}], [{"name": "read_file"}] * 2]
)
def test_invalid_tool_configuration_fails_before_a_model_call(state, schemas):
    state.config.tool_schemas = schemas
    assert start(state.config, state.prompt).error.code == "invalid_configuration"
    assert advance(state, model()).error.code == "invalid_configuration"
