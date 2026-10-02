"""SDK additions retain legacy payloads and freeze explicit operation inputs."""

from uuid import uuid4

import pytest
from durable_agent.contracts import (
    ModelRequest,
    SubmitFollowUpRequest,
    SubmitRunRequest,
    ToolRequest,
)
from durable_agent.sdk import ToolCall
from pydantic import ValidationError


def test_legacy_requests_retain_inferred_configuration():
    model = ModelRequest(prompt="Draft", history=[])
    tool = ToolRequest(
        call={"call_id": "call_a", "name": "run_command", "arguments": {"command": "true"}},
        input_version_id=uuid4(),
    )
    assert model.explicit_input is None
    assert model.tool_schemas is None
    assert tool.timeout_seconds is None
    assert ToolCall(name="run_command").timeout_seconds is None
    assert SubmitRunRequest(prompt="Draft", submission_key=uuid4()).agent is None


def test_explicit_model_input_preserves_empty_tool_selection_and_native_context():
    request = ModelRequest(
        prompt="",
        history=[],
        model="custom-model",
        model_params={"max_output_tokens": 100},
        tool_schemas=[],
        timeout_seconds=300,
        explicit_input=[
            {"type": "reasoning", "encrypted_content": "opaque"},
            {"role": "user", "content": "Draft"},
        ],
    )
    restored = ModelRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    assert restored.tool_schemas == []
    assert restored.explicit_input[0]["encrypted_content"] == "opaque"


def test_sdk_input_supports_explicit_null_and_follow_up_inheritance():
    body = SubmitRunRequest(agent="writer", input=None, submission_key=uuid4())
    assert "input" in body.model_fields_set
    follow_up = SubmitFollowUpRequest(
        input={"edit": "shorter"}, submission_key=uuid4(), after_run_id=uuid4()
    )
    assert follow_up.agent is None
    assert follow_up.input == {"edit": "shorter"}


@pytest.mark.parametrize(
    "fields", [{}, {"prompt": "Draft", "input": "Draft"}, {"agent": "writer", "prompt": "Draft"}]
)
def test_ambiguous_submission_is_rejected(fields):
    with pytest.raises(ValidationError):
        SubmitRunRequest(submission_key=uuid4(), **fields)


def test_legacy_submission_with_serialized_defaults_still_uses_prompt():
    original = SubmitRunRequest(prompt="Draft", submission_key=uuid4())
    restored = SubmitRunRequest.model_validate_json(original.model_dump_json())
    assert restored.prompt == "Draft"
    assert not restored.is_sdk_input


@pytest.mark.parametrize(
    "path,extra",
    [
        ("/api/runs", {}),
        (f"/api/conversations/{uuid4()}/runs", {"after_run_id": str(uuid4())}),
        (f"/api/runs/{uuid4()}/forks", {"after_operation_id": str(uuid4())}),
    ],
)
def test_custom_submission_never_silently_runs_the_legacy_agent(path, extra):
    import asyncio

    import httpx
    from durable_agent.control.app import app

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://control"
        ) as client:
            response = await client.post(
                path,
                json={
                    "submission_key": str(uuid4()),
                    "agent": "writer",
                    "input": "Draft",
                    **extra,
                },
            )
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "sdk_unavailable"

    asyncio.run(check())
