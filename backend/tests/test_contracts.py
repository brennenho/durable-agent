"""Contract boundaries shared by the independently implemented components."""

import json
from datetime import UTC
from uuid import UUID

import pytest
from durable_agent.contracts import (
    AgentConfig,
    Assignment,
    ClaimRequest,
    FailedOutcome,
    ModelRequest,
    ModelResult,
    OperationResult,
    ReportRequest,
    SuccessfulOutcome,
    ToolError,
    ToolResult,
    WriteFileArguments,
)
from durable_agent.harness.contracts import ContinueTransition, Transition
from pydantic import TypeAdapter, ValidationError

VERSION = "00000000-0000-4000-8000-000000000001"
WORKER = "00000000-0000-4000-8000-000000000002"
CONFIG = {
    "model": "test-model",
    "model_params": {"max_output_tokens": 4096},
    "tool_schemas": [{"type": "function", "name": "write_file", "parameters": {"type": "object"}}],
    "initial_workspace_version_id": VERSION,
    "sandbox": {"image_digest": "sha256:test", "workspace_max_bytes": 1024, "log_max_bytes": 1024},
    "max_attempts": 5,
    "max_turns": 20,
    "call_timeout_seconds": 120,
}
MODEL_RESULT = {
    "kind": "model",
    "output": {
        "status": "completed",
        "text": "",
        "tool_calls": [
            {
                "call_id": "call_write",
                "name": "write_file",
                "arguments": {"path": "a.md", "content": "Hi"},
            }
        ],
    },
    "native_context": [
        {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "opaque"},
        {
            "type": "function_call",
            "call_id": "call_write",
            "name": "write_file",
            "arguments": '{"path":"a.md","content":"Hi"}',
        },
    ],
}


def test_model_context_survives_json_round_trip():
    result = ModelResult.model_validate_json(json.dumps(MODEL_RESULT))
    assert result.model_dump(mode="json") == MODEL_RESULT
    assert result.output.tool_calls[0].call_id == result.native_context[1]["call_id"]


def test_model_facing_tool_error_is_still_a_successful_execution():
    report = ReportRequest.model_validate(
        {
            "worker_id": WORKER,
            "lease_generation": 1,
            "outcome": {
                "status": "succeeded",
                "result": {
                    "kind": "tool",
                    "call_id": "call_read",
                    "output": {"status": "error", "code": "not_found", "message": "Missing file"},
                    "workspace_version_id": VERSION,
                },
            },
        }
    )
    assert isinstance(report.outcome, SuccessfulOutcome)
    assert isinstance(report.outcome.result, ToolResult)
    assert isinstance(report.outcome.result.output, ToolError)
    assert report.outcome.result.workspace_version_id == UUID(VERSION)


def test_infrastructure_failure_has_no_completed_tool_result():
    payload = {
        "worker_id": WORKER,
        "lease_generation": 2,
        "outcome": {
            "status": "failed",
            "error": {"code": "timeout", "message": "Sandbox timed out", "retryable": True},
        },
    }
    report = ReportRequest.model_validate(payload)
    assert isinstance(report.outcome, FailedOutcome)
    assert report.outcome.error.retryable is True
    assert report.outcome.error.retry_after_seconds is None
    payload["outcome"]["result"] = MODEL_RESULT
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReportRequest.model_validate(payload)


@pytest.mark.parametrize("kind", [None, "unknown", "tool"])
def test_result_discriminator_rejects_missing_unknown_or_mismatched_kind(kind):
    payload = {**MODEL_RESULT, "kind": kind}
    if kind is None:
        del payload["kind"]
    with pytest.raises(ValidationError):
        TypeAdapter(OperationResult).validate_python(payload)


@pytest.mark.parametrize(
    "contract,payload",
    [
        (ClaimRequest, {"worker_id": WORKER, "lease_generation": 1}),
        (WriteFileArguments, {"path": "a.md", "content": "Hi", "append": True}),
        (AgentConfig, {**CONFIG, "sandbox": {**CONFIG["sandbox"], "network": True}}),
    ],
)
def test_unknown_fields_are_rejected_at_nested_boundaries(contract, payload):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        contract.model_validate(payload)


@pytest.mark.parametrize("limit", ["max_attempts", "max_turns", "call_timeout_seconds"])
def test_execution_budgets_must_be_positive(limit):
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({**CONFIG, limit: 0})


def test_assignment_preserves_the_fixed_tool_input_and_normalizes_time():
    assignment = Assignment.model_validate(
        {
            "run_id": VERSION,
            "operation_id": VERSION,
            "attempt_id": VERSION,
            "attempt_no": 1,
            "lease_generation": 1,
            "lease_expires_at": "2026-10-01T13:00:30-07:00",
            "deadline_at": "2026-10-01T13:02:00-07:00",
            "server_time": "2026-10-01T13:00:00-07:00",
            "config": CONFIG,
            "operation": {
                "kind": "tool",
                "request": {
                    "call": MODEL_RESULT["output"]["tool_calls"][0],
                    "input_version_id": VERSION,
                },
            },
        }
    )
    wire = assignment.model_dump(mode="json")
    assert wire["server_time"] == "2026-10-01T20:00:00Z"
    assert assignment.deadline_at.tzinfo is UTC
    assert wire["operation"]["request"]["input_version_id"] == VERSION
    wire["deadline_at"] = "2026-10-01T20:02:00"
    with pytest.raises(ValidationError, match="timezone_aware"):
        Assignment.model_validate(wire)


def test_harness_plans_a_tool_without_assigning_its_workspace():
    transition = TypeAdapter(Transition).validate_python(
        {
            "status": "continue",
            "next": {"kind": "tool", "call": MODEL_RESULT["output"]["tool_calls"][0]},
        }
    )
    assert isinstance(transition, ContinueTransition)
    payload = transition.model_dump(mode="json")
    payload["next"]["input_version_id"] = VERSION
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TypeAdapter(Transition).validate_python(payload)


def test_previous_turns_preserve_context_and_old_model_requests_still_parse():
    previous = {
        "prompt": "First prompt",
        "history": [MODEL_RESULT],
        "terminal_error": {
            "code": "call_timeout",
            "message": "Tool interrupted",
            "retryable": True,
        },
    }
    request = ModelRequest(prompt="Continue", history=[], previous_turns=[previous])
    restored = ModelRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    assert restored.previous_turns[0].history[0].native_context == MODEL_RESULT["native_context"]
    assert restored.previous_turns[0].terminal_error.code == "call_timeout"
    assert (
        ModelRequest.model_validate({"prompt": "Existing run", "history": []}).previous_turns == []
    )
