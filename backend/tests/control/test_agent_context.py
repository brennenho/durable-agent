from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.control.agent_state import restore_context
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.db.models import Operation, Run
from durable_agent.sdk import Done, StepResult
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.registration import agent


@agent(name="count", version="1")
class Counter:
    def start(self, ctx):
        return Done(output=ctx.input)

    def advance(self, ctx, result):
        return Done(output=result.value)


def test_agent_runtime_freezes_identity_and_rejects_changed_deployment(config):
    runtime = AgentRuntime(AgentApp(agents=[Counter]), "example:app", "build-1")
    saved = runtime.freeze(config, "count")
    assert saved.agent.model_dump() == {
        "name": "count",
        "version": "1",
        "app_ref": "example:app",
        "build_id": "build-1",
    }
    assert isinstance(runtime.instance(saved), Counter)
    for changed in (
        AgentRuntime(runtime.app, "other:app", "build-1"),
        AgentRuntime(runtime.app, "example:app", "build-2"),
        AgentRuntime(AgentApp(), "example:app", "build-1"),
    ):
        with pytest.raises(ControlError) as error:
            changed.instance(saved)
        assert error.value.error.code == "agent_incompatible"
    with pytest.raises(ControlError) as error:
        runtime.freeze(config, "missing")
    assert error.value.error.code == "unknown_agent"
    saved.tool_definitions["new"] = None
    with pytest.raises(ControlError, match="Saved agent deployment"):
        runtime.instance(saved)


def test_context_uses_only_accepted_results_and_preserves_inherited_history(config):
    prior = StepResult(operation_id=uuid4(), kind="model", value={"text": "old"})
    config = AgentRuntime().freeze(config).model_copy(update={"agent_history": [prior]})
    workspace = uuid4()
    run = Run(
        id=uuid4(), config=config.model_dump(mode="json"), agent_input=[1], agent_state={"count": 2}
    )
    accepted = Operation(
        id=uuid4(),
        kind="tool",
        request={"call": {"name": "read_file"}},
        completed_at=datetime.now(UTC),
        result={
            "kind": "tool",
            "call_id": "read",
            "output": {"status": "ok", "value": {"text": "new"}},
            "workspace_version_id": str(workspace),
        },
    )
    pending = Operation(id=uuid4(), kind="model", request={}, completed_at=None)
    ctx = restore_context(run, [accepted, pending])
    assert ctx.input == [1]
    assert ctx.state == {"count": 2}
    assert ctx.workspace_version_id == workspace
    assert [item.operation_id for item in ctx.history] == [prior.operation_id, accepted.id]
    assert ctx.history[-1].value == {"text": "new"}
    assert ctx.limits.max_steps == config.max_steps
