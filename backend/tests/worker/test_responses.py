import pytest
from durable_agent.contracts import FailedOutcome, SuccessfulOutcome
from durable_agent.worker.responses import map_response
from openai.types.responses import Response


def response(payload):
    parsed = Response.model_validate(payload)
    parsed._request_id = "req_fixture"
    return parsed


def test_preserves_native_context_tool_order_and_request_metadata(response_payload):
    outcome = map_response(response(response_payload))
    assert isinstance(outcome, SuccessfulOutcome)
    assert outcome.provider_request_id == "req_fixture"
    assert outcome.usage == response_payload["usage"]
    assert outcome.result.native_context == response_payload["output"]
    assert outcome.result.output.text == "I will inspect the files."
    assert [call.call_id for call in outcome.result.output.tool_calls] == ["call_list", "call_read"]
    assert outcome.result.output.tool_calls[1].arguments == {"path": "README.md"}
    # The provider input snapshot is independent of mutations to the normalized plan.
    outcome.result.output.tool_calls[1].arguments["path"] = "other.txt"
    assert outcome.result.native_context == response_payload["output"]


@pytest.mark.parametrize("refusal", [False, True])
def test_final_text_and_refusals_without_usage(response_payload, refusal):
    message = response_payload["output"][1]
    message["phase"] = "final_answer"
    message["content"] = (
        [{"type": "refusal", "refusal": "Cannot do that."}]
        if refusal
        else [{"type": "output_text", "text": "Done.", "annotations": []}]
    )
    response_payload["output"] = [message]
    response_payload.pop("usage")
    outcome = map_response(Response.model_validate(response_payload))
    assert isinstance(outcome, SuccessfulOutcome)
    assert outcome.result.output.text == ("Cannot do that." if refusal else "Done.")
    assert outcome.result.output.tool_calls == []
    assert outcome.provider_request_id is None
    assert outcome.usage is None


def test_incomplete_response_preserves_partial_context_without_tool_execution(response_payload):
    response_payload["status"] = "incomplete"
    response_payload["output"][-1]["arguments"] = '{"path":'
    outcome = map_response(response(response_payload))
    assert isinstance(outcome, SuccessfulOutcome)
    assert outcome.result.output.status == "incomplete"
    assert outcome.result.output.tool_calls == []
    assert outcome.result.native_context == response_payload["output"]


@pytest.mark.parametrize("arguments", ['{"path":', "[]", "null", '{"x":NaN}'])
def test_invalid_completed_arguments_fail_the_whole_result(response_payload, arguments):
    response_payload["output"][-1]["arguments"] = arguments
    outcome = map_response(response(response_payload))
    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.code == "invalid_model_response"
    assert not outcome.error.retryable
    assert outcome.provider_request_id == "req_fixture"


def test_incomplete_tool_item_cannot_be_accepted_as_completed(response_payload):
    response_payload["output"][-1]["status"] = "incomplete"
    assert isinstance(map_response(response(response_payload)), FailedOutcome)


@pytest.mark.parametrize("code,retryable", [("server_error", True), ("invalid_prompt", False)])
def test_provider_failures_are_not_successful_results(response_payload, code, retryable):
    response_payload.update(status="failed", error={"code": code, "message": "Provider failure"})
    outcome = map_response(response(response_payload))
    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.code == f"openai_{code}"
    assert outcome.error.retryable is retryable
    assert outcome.provider_request_id == "req_fixture"


@pytest.mark.parametrize("status", ["queued", "in_progress", "cancelled", "failed"])
def test_unexpected_response_status_cannot_complete_an_operation(response_payload, status):
    response_payload["status"] = status
    assert isinstance(map_response(response(response_payload)), FailedOutcome)


@pytest.mark.parametrize("field", ["status", "output"])
def test_missing_response_fields_fail_without_escaping_mapper(response_payload, field):
    response_payload.pop(field)
    # The SDK constructs responses without strict validation.
    outcome = map_response(Response.construct(**response_payload))
    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.code == "invalid_model_response"


def test_malformed_provider_error_keeps_request_id(response_payload):
    response_payload.update(status="failed", error={"code": "server_error"})
    parsed = Response.construct(**response_payload)
    parsed._request_id = "req_malformed"
    outcome = map_response(parsed)
    assert isinstance(outcome, FailedOutcome)
    assert outcome.error.code == "invalid_model_response"
    assert outcome.provider_request_id == "req_malformed"
