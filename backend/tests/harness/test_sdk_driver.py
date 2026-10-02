from uuid import uuid4

import pytest
from durable_agent.contracts import AgentConfig
from durable_agent.harness.sdk_driver import invoke, plan_step
from durable_agent.sdk import (
    AgentContext,
    ChildBatch,
    Done,
    Fail,
    ModelCall,
    Next,
    StepResult,
    ToolCall,
)
from pydantic import ValidationError


@pytest.fixture
def context():
    return AgentContext(
        run_id=uuid4(),
        agent="test",
        version="1",
        input={"items": [1]},
        state={"count": 0},
        workspace_version_id=uuid4(),
    )


@pytest.fixture
def config(context):
    return AgentConfig(
        model="test",
        model_params={},
        tool_schemas=[{"name": "read_file"}],
        initial_workspace_version_id=context.workspace_version_id,
        sandbox={"image_digest": "sha256:test", "workspace_max_bytes": 1024, "log_max_bytes": 1024},
        max_attempts=5,
        max_turns=20,
        call_timeout_seconds=120,
    )


def test_callback_cannot_mutate_persisted_input_state_or_result(context):
    result = StepResult(operation_id=uuid4(), kind="tool", value={"items": [1]})

    def advance(ctx, accepted):
        ctx.input["items"].append(2)
        ctx.state["count"] = 1
        accepted.value["items"].append(3)
        return Next(state=ctx.state, step=ToolCall(name="read_file", arguments={"path": "a"}))

    decision = invoke(advance, context, result)

    assert decision.state == {"count": 1}
    assert context.input == {"items": [1]}
    assert context.state == {"count": 0}
    assert result.value == {"items": [1]}


@pytest.mark.parametrize("explicit", [False, True])
def test_done_preserves_omitted_versus_explicit_null_state(context, explicit):
    decision = invoke(
        lambda ctx: Done(output=[1], **({"state": None} if explicit else {})), context
    )
    assert decision.output == [1]
    assert ("state" in decision.model_fields_set) is explicit


def test_callback_exception_and_invalid_decision_fail_explicitly(context):
    def broken(ctx):
        raise RuntimeError("bad callback")

    assert invoke(broken, context) == Fail(
        code="agent_callback_failed", message="RuntimeError: bad callback"
    )
    assert invoke(lambda ctx: {"status": "done"}, context).code == "agent_callback_failed"


def plan(step, config, **kwargs):
    return plan_step(
        step,
        config,
        uuid4(),
        config.initial_workspace_version_id,
        steps_taken=0,
        can_delegate=True,
        **kwargs,
    )


def test_model_step_freezes_selected_tools_and_explicit_input(config):
    step = ModelCall(
        prompt="Next",
        context=[{"role": "system", "content": "Be brief"}],
        tools=["read_file"],
        model="other",
        parameters={"temperature": 0},
        timeout_seconds=45,
    )
    request = plan(step, config).request
    assert request.explicit_input == [*step.context, {"role": "user", "content": "Next"}]
    assert request.tool_schemas == [{"name": "read_file"}]
    assert request.model == "other"
    assert request.model_params == {"temperature": 0}
    assert request.timeout_seconds == 45


@pytest.mark.parametrize("tools", [["missing"], ["read_file", "read_file"]])
def test_model_cannot_expose_unknown_or_duplicate_tools(config, tools):
    with pytest.raises(ValueError, match="unique registered"):
        plan(ModelCall(tools=tools), config)


def test_tool_ids_preserve_provider_identity_or_use_operation_id(config):
    operation_id = uuid4()
    for call_id in (None, "provider-call"):
        request = plan_step(
            ToolCall(name="read_file", call_id=call_id),
            config,
            operation_id,
            config.initial_workspace_version_id,
            steps_taken=0,
            can_delegate=True,
        ).request
        assert request.call.call_id == (call_id or str(operation_id))
        assert request.input_version_id == config.initial_workspace_version_id
        assert request.timeout_seconds is None
    with pytest.raises(ValidationError):
        ToolCall(name="read_file", call_id="")


def test_child_batch_keeps_structured_input_and_uses_coordinator(config):
    step = ChildBatch(tasks=[{"name": "research", "agent": "researcher", "input": ["topic"]}])
    operation = plan(step, config)
    assert operation.request.call.name == "delegate"
    assert operation.request.call.arguments["tasks"] == [
        {"name": "research", "agent": "researcher", "input": ["topic"], "prompt": "research"}
    ]
    with pytest.raises(ValueError, match="children cannot delegate"):
        plan_step(
            step,
            config,
            uuid4(),
            config.initial_workspace_version_id,
            steps_taken=0,
            can_delegate=False,
        )


def test_step_limit_prevents_scheduling_but_callbacks_can_still_finish(config):
    with pytest.raises(ValueError, match="step limit"):
        plan_step(
            ModelCall(),
            config,
            uuid4(),
            config.initial_workspace_version_id,
            steps_taken=config.max_steps,
            can_delegate=True,
        )
    with pytest.raises(ValueError, match="not registered"):
        plan(ToolCall(name="missing"), config)
