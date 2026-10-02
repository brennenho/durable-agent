"""Decisions can cross a persistence boundary without executable Python objects."""

from uuid import uuid4

import pytest
from durable_agent.sdk import AgentContext, ChildBatch, Done, ModelCall, Next, ToolCall
from durable_agent.sdk.types import Decision
from pydantic import TypeAdapter, ValidationError


def test_round_trip_preserves_decision_and_copies_nested_state():
    state = {"draft": {"text": "Saved draft"}}
    decision = Next(state=state, step=ToolCall(name="save", arguments={"text": "Saved draft"}))
    encoded = decision.model_dump_json()
    state["draft"]["text"] = "Changed by caller"
    restored = TypeAdapter(Decision).validate_json(encoded)
    assert restored.state == {"draft": {"text": "Saved draft"}}
    assert restored.step.name == "save"
    assert restored.step.arguments == {"text": "Saved draft"}


@pytest.mark.parametrize("state", [object(), {"bad": float("nan")}, {"bad": float("inf")}])
def test_state_rejects_non_json_values(state):
    with pytest.raises(ValidationError):
        Next(state=state, step=ModelCall(prompt="Draft"))


def test_done_distinguishes_preserved_state_from_explicit_null():
    omitted = Done(output="Done")
    cleared = Done(output="Done", state=None)
    adapter = TypeAdapter(Decision)
    assert "state" not in adapter.validate_json(omitted.model_dump_json()).model_fields_set
    assert "state" in adapter.validate_json(cleared.model_dump_json()).model_fields_set


@pytest.mark.parametrize("seconds", [0, 901, True, 1.5])
def test_tool_deadline_rejects_invalid_budgets(seconds):
    with pytest.raises(ValidationError):
        ToolCall(name="save", timeout_seconds=seconds)


def test_context_round_trip_preserves_identity_history_and_limits():
    operation_id = uuid4()
    context = AgentContext(
        run_id=uuid4(),
        agent="writer",
        version="1",
        input={"task": "Draft"},
        workspace_version_id=uuid4(),
        state={"phase": "review"},
        history=[{"operation_id": operation_id, "kind": "model", "value": {"text": "Draft"}}],
    )
    restored = AgentContext.model_validate_json(context.model_dump_json())
    assert restored == context
    assert restored.history[0].operation_id == operation_id
    assert restored.limits.max_steps == 100
    with pytest.raises(ValidationError):
        restored.state = {}


@pytest.mark.parametrize("count", [0, 4])
def test_child_batches_keep_existing_size_limit(count):
    with pytest.raises(ValidationError):
        ChildBatch(
            tasks=[{"name": f"task{i}", "agent": "writer", "input": "Draft"} for i in range(count)]
        )
