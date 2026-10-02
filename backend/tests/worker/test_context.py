import json
from copy import deepcopy
from uuid import uuid4

import pytest
from durable_agent.contracts import (
    ExecutionError,
    ModelRequest,
    PreviousTurn,
    SuccessfulOutcome,
    ToolResult,
    ToolSuccess,
)
from durable_agent.harness.model_input import InvalidModelInput, response_input
from durable_agent.worker.responses import map_response
from openai.types.responses import Response

ERROR = ExecutionError(code="turn_limit", message="Model turn limit reached", retryable=False)


def saved_model(payload):
    outcome = map_response(Response.model_validate(payload))
    assert isinstance(outcome, SuccessfulOutcome)
    return outcome.result


def tool_result(call_id):
    return ToolResult(
        kind="tool",
        call_id=call_id,
        output=ToolSuccess(status="ok", value={"content": "Saved"}),
        workspace_version_id=uuid4(),
    )


def wire_output(result):
    return {
        "type": "function_call_output",
        "call_id": result.call_id,
        "output": result.output.model_dump_json(),
    }


def test_replays_previous_turns_then_current_history_in_order(response_payload):
    final = deepcopy(response_payload)
    final["output"] = final["output"][:2]
    for item in final["output"]:
        item["id"] += "_final"
    final["output"][1]["phase"] = "final_answer"
    previous = saved_model(final)
    current = saved_model(response_payload)
    tools = [tool_result("call_list"), tool_result("call_read")]
    request = ModelRequest(
        prompt="Current prompt",
        history=[current, *tools],
        previous_turns=[
            PreviousTurn(prompt="First prompt", history=[previous]),
            PreviousTurn(prompt="Failed prompt", history=[], terminal_error=ERROR),
        ],
    )
    before = request.model_dump()
    items = response_input(request)
    assert items[:4] == [
        {"role": "user", "content": "First prompt"},
        *previous.native_context,
        {"role": "user", "content": "Failed prompt"},
    ]
    assert "Runtime status" in items[4]["content"]
    assert ERROR.message in items[4]["content"]
    assert items[5:] == [
        {"role": "user", "content": "Current prompt"},
        *response_payload["output"],
        *[wire_output(tool) for tool in tools],
    ]
    assert request.model_dump() == before


def test_closes_only_unanswered_calls_without_changing_saved_results(response_payload):
    accepted = tool_result("call_list")
    request = ModelRequest(
        prompt="Continue",
        history=[],
        previous_turns=[
            PreviousTurn(
                prompt="Inspect files",
                history=[saved_model(response_payload), accepted],
                terminal_error=ERROR,
            )
        ],
    )
    before = request.model_dump()
    items = response_input(request)
    assert items[1:5] == response_payload["output"]
    assert items[5] == wire_output(accepted)
    assert items[6]["type"] == "function_call_output"
    assert items[6]["call_id"] == "call_read"
    interruption = json.loads(items[6]["output"])
    assert interruption["status"] == "error"
    assert interruption["code"] == "interrupted"
    assert "No accepted result" in interruption["message"]
    assert ERROR.message in items[7]["content"]
    assert items[8] == {"role": "user", "content": "Continue"}
    assert request.model_dump() == before
    assert response_input(request) == items


@pytest.mark.parametrize("failure", ["incomplete", "partial_arguments", "duplicate", "reused"])
def test_unreplayable_final_response_uses_text_record_and_keeps_valid_prefix(
    response_payload, failure
):
    valid = saved_model(response_payload)
    tools = [tool_result("call_list"), tool_result("call_read")]
    broken = deepcopy(response_payload)
    for item in broken["output"]:
        item["id"] += "_broken"
        if item["type"] == "function_call" and failure != "reused":
            item["call_id"] += "_broken"
    if failure in {"incomplete", "partial_arguments"}:
        broken["status"] = "incomplete"
    if failure == "partial_arguments":
        broken["output"][-1]["arguments"] = '{"path":'
    if failure == "duplicate":
        broken["output"][-1]["call_id"] = broken["output"][-2]["call_id"]
    partial = saved_model(broken)
    request = ModelRequest(
        prompt="Try again",
        history=[],
        previous_turns=[
            PreviousTurn(
                prompt="Inspect files", history=[valid, *tools, partial], terminal_error=ERROR
            )
        ],
    )
    before = request.model_dump()
    items = response_input(request)
    assert items[1:7] == [*valid.native_context, *[wire_output(tool) for tool in tools]]
    fallback = items[7]
    assert fallback["role"] == "assistant"
    assert "not a completed answer or tool result" in fallback["content"]
    assert json.loads(fallback["content"].split("\n", 1)[1]) == {
        "text": partial.output.text,
        "tool_intent": broken["output"][-2:],
    }
    assert ERROR.message in items[8]["content"]
    assert items[9] == {"role": "user", "content": "Try again"}
    assert request.model_dump() == before


@pytest.mark.parametrize("history_kind", ["unanswered", "orphan", "out_of_order", "early_model"])
def test_rejects_inconsistent_current_history(response_payload, history_kind):
    model = saved_model(response_payload)
    histories = {
        "unanswered": [model],
        "orphan": [tool_result("missing")],
        "out_of_order": [model, tool_result("call_read")],
        "early_model": [model, model],
    }
    with pytest.raises(InvalidModelInput):
        response_input(ModelRequest(prompt="Inspect files", history=histories[history_kind]))


@pytest.mark.parametrize("has_later_result", [False, True])
def test_fallback_cannot_hide_accepted_tools_or_repair_a_successful_turn(
    response_payload, has_later_result
):
    response_payload["status"] = "incomplete"
    history = [saved_model(response_payload)]
    if has_later_result:
        history.append(tool_result("call_list"))
    turn = PreviousTurn(
        prompt="Inspect files", history=history, terminal_error=ERROR if has_later_result else None
    )
    with pytest.raises(InvalidModelInput):
        response_input(ModelRequest(prompt="Continue", history=[], previous_turns=[turn]))
