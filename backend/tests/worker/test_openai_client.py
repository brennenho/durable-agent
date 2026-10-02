import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from uuid import uuid4

import httpx
import pytest
from durable_agent.contracts import (
    FailedOutcome,
    ModelRequest,
    SuccessfulOutcome,
    ToolResult,
    ToolSuccess,
)
from durable_agent.worker.openai_client import OpenAIClient, retry_after_seconds
from durable_agent.worker.responses import map_response
from openai.types.responses import Response


def run_client(config, handler, request=None, timeout=1):
    async def exercise():
        client = OpenAIClient(
            "test-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        try:
            return await client.execute(
                config,
                request or ModelRequest(prompt="Edit a file", history=[]),
                timeout_seconds=timeout,
            )
        finally:
            await client.close()

    return asyncio.run(exercise())


def test_rebuilds_request_from_saved_context_and_tool_outputs(config, response_payload):
    previous = map_response(Response.model_validate(response_payload)).result
    tool = ToolResult(
        kind="tool",
        call_id="call_read",
        output=ToolSuccess(status="ok", value={"content": "Saved"}),
        workspace_version_id=uuid4(),
    )
    listing = tool.model_copy(update={"call_id": "call_list"})
    request = ModelRequest(prompt="Edit a file", history=[previous, listing, tool])

    def handler(http_request):
        body = json.loads(http_request.content)
        assert body["input"][0] == {"role": "user", "content": "Edit a file"}
        assert body["input"][1:-2] == response_payload["output"]
        assert body["input"][-1] == {
            "type": "function_call_output",
            "call_id": "call_read",
            "output": tool.output.model_dump_json(),
        }
        assert "workspace_version_id" not in http_request.content.decode()
        assert body["max_output_tokens"] == 4096
        assert body["stream"] is body["background"] is body["store"] is False
        assert body["include"] == ["reasoning.encrypted_content"]
        return httpx.Response(200, json=response_payload, headers={"x-request-id": "req_http"})

    outcome = run_client(config, handler, request)
    assert isinstance(outcome, SuccessfulOutcome)
    assert outcome.provider_request_id == "req_http"
    assert outcome.usage == response_payload["usage"]


def test_invalid_history_fails_before_sending_a_request(config, response_payload):
    model = map_response(Response.model_validate(response_payload)).result

    def handler(request):
        pytest.fail("Invalid history must not reach the provider")

    outcome = run_client(config, handler, ModelRequest(prompt="Continue", history=[model]))
    assert outcome.error.code == "invalid_model_input"
    assert not outcome.error.retryable


@pytest.mark.parametrize("status,retryable", [(401, False), (422, False), (429, True), (503, True)])
def test_http_failure_is_reported_once_without_sdk_retries(config, status, retryable):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"error": {"message": "Unavailable"}},
            headers={"x-request-id": "req_error", "retry-after": "3"},
        )

    outcome = run_client(config, handler)
    assert isinstance(outcome, FailedOutcome)
    assert len(requests) == 1
    assert outcome.error.retryable is retryable
    assert outcome.error.retry_after_seconds == 3
    assert outcome.provider_request_id == "req_error"


def test_connection_error_is_reported_without_repeating_request(config):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadError("Connection lost", request=request)

    outcome = run_client(config, handler)
    assert len(requests) == 1
    assert outcome.error.retryable


def test_invalid_provider_json_is_a_response_error(config):
    def handler(request):
        return httpx.Response(200, content="{broken}", headers={"content-type": "application/json"})

    outcome = run_client(config, handler)
    assert outcome.error.code == "invalid_model_response"
    assert not outcome.error.retryable


def test_total_deadline_cancels_a_stalled_response(config):
    cancelled = []

    async def handler(request):
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    outcome = run_client(config, handler, timeout=0.01)
    assert outcome.error.code == "call_timeout"
    assert cancelled == [True]


@pytest.mark.parametrize(
    "key", ["input", "stream", "extra_body", "timeout", "previous_response_id"]
)
def test_model_parameters_cannot_override_execution_fields(config, key):
    config.model_params[key] = None

    def handler(request):
        pytest.fail("Invalid configuration must not issue a request")

    assert run_client(config, handler).error.code == "invalid_model_config"


@pytest.mark.parametrize("value", [None, "invalid", "nan", "inf"])
def test_invalid_retry_after_is_ignored(value):
    assert retry_after_seconds(value) is None


def test_retry_after_accepts_http_date():
    value = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    assert 28 < retry_after_seconds(value) <= 30


def test_sdk_request_uses_saved_input_and_selected_model_tools(config, response_payload):
    explicit = [
        {"role": "developer", "content": "Return a short report."},
        {"role": "user", "content": "Review the saved document."},
    ]
    tools = [{"type": "function", "name": "review", "parameters": {"type": "object"}}]
    request = ModelRequest(
        prompt="Not part of the explicit input",
        history=[],
        explicit_input=explicit,
        model="selected-model",
        model_params={"max_output_tokens": 512},
        tool_schemas=tools,
    )
    original = request.model_dump()

    def handler(http_request):
        body = json.loads(http_request.content)
        assert body["input"] == explicit
        assert body["model"] == "selected-model"
        assert body["max_output_tokens"] == 512
        assert body["tools"] == tools
        assert body["stream"] is body["background"] is body["store"] is False
        return httpx.Response(200, json=response_payload)

    assert isinstance(run_client(config, handler, request), SuccessfulOutcome)
    assert request.model_dump() == original


def test_empty_sdk_fields_do_not_fall_back_to_legacy_defaults(config, response_payload):
    config.tool_schemas = [{"name": "unexpected_tool"}]
    request = ModelRequest(
        prompt="Do not append this prompt",
        history=[],
        explicit_input=[],
        model_params={},
        tool_schemas=[],
    )

    def handler(http_request):
        body = json.loads(http_request.content)
        assert body["input"] == []
        assert body["tools"] == []
        assert body["model"] == config.model
        assert "max_output_tokens" not in body
        return httpx.Response(200, json=response_payload)

    assert isinstance(run_client(config, handler, request), SuccessfulOutcome)


@pytest.mark.parametrize("key", ["stream", "timeout", "extra_body", "previous_response_id"])
def test_sdk_parameters_cannot_override_execution_fields(config, key):
    request = ModelRequest(prompt="", history=[], explicit_input=[], model_params={key: None})

    def handler(request):
        pytest.fail("Invalid configuration must not reach the provider")

    outcome = run_client(config, handler, request)
    assert outcome.error.code == "invalid_model_config"
    assert not outcome.error.retryable


def test_sdk_provider_failure_is_reported_without_retrying(config):
    requests = []
    request = ModelRequest(
        prompt="", history=[], explicit_input=[{"role": "user", "content": "Hi"}]
    )

    def handler(http_request):
        requests.append(http_request)
        return httpx.Response(429, json={"error": {"message": "Unavailable"}})

    outcome = run_client(config, handler, request)
    assert outcome.error.retryable
    assert outcome.error.code == "openai_http_429"
    assert len(requests) == 1


def test_sdk_request_cannot_extend_the_assigned_deadline(config):
    cancelled = []
    request = ModelRequest(prompt="", history=[], explicit_input=[], timeout_seconds=900)

    async def handler(http_request):
        try:
            await asyncio.Future()
        finally:
            cancelled.append(True)

    outcome = run_client(config, handler, request, timeout=0.01)
    assert outcome.error.code == "call_timeout"
    assert cancelled == [True]
