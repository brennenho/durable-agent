from uuid import uuid4

from durable_agent.contracts import AgentConfig, ModelResult, ToolResult
from durable_agent.harness.builtin import BuiltinAgent
from durable_agent.harness.sdk_driver import invoke, plan_step
from durable_agent.harness.sdk_results import to_step_result
from durable_agent.sdk import AgentContext, Done, Next


def test_builtin_sdk_loop_preserves_tool_identity_history_and_followup():
    workspace = uuid4()
    config = AgentConfig(
        model="test",
        model_params={},
        tool_schemas=[{"name": "read_file"}],
        initial_workspace_version_id=workspace,
        sandbox={"image_digest": "sha256:test", "workspace_max_bytes": 1024, "log_max_bytes": 1024},
        max_attempts=5,
        max_turns=20,
        call_timeout_seconds=120,
    )
    agent = BuiltinAgent(config)
    ctx = AgentContext(
        run_id=uuid4(), agent="builtin", version="1", input="Read a", workspace_version_id=workspace
    )
    first = invoke(agent.start, ctx)
    assert isinstance(first, Next)
    assert first.step.context == [{"role": "user", "content": "Read a"}]
    model = ModelResult(
        kind="model",
        output={
            "status": "completed",
            "text": "",
            "tool_calls": [{"call_id": "read-1", "name": "read_file", "arguments": {"path": "a"}}],
        },
        native_context=[
            {
                "type": "function_call",
                "call_id": "read-1",
                "name": "read_file",
                "arguments": '{"path":"a"}',
            }
        ],
    )
    ctx = ctx.model_copy(update={"state": first.state})
    second = invoke(agent.advance, ctx, to_step_result(uuid4(), model, workspace))
    operation = plan_step(second.step, config, uuid4(), workspace, steps_taken=1, can_delegate=True)
    assert operation.request.call.call_id == "read-1"
    tool = ToolResult(
        kind="tool",
        call_id="read-1",
        output={"status": "ok", "value": {"content": "hello"}},
        workspace_version_id=workspace,
    )
    ctx = ctx.model_copy(
        update={"state": second.state, "history": (to_step_result(uuid4(), model, workspace),)}
    )
    resumed = invoke(agent.start, ctx.model_copy(update={"input": "Continue"}))
    assert resumed.step.context[2]["call_id"] == "read-1"
    assert '"code":"interrupted"' in resumed.step.context[2]["output"]
    third = invoke(agent.advance, ctx, to_step_result(uuid4(), tool, workspace))
    assert third.step.context[-1]["type"] == "function_call_output"
    assert third.step.context[-1]["call_id"] == "read-1"
    final = ModelResult(
        kind="model",
        output={"status": "completed", "text": "hello", "tool_calls": []},
        native_context=[{"type": "message", "role": "assistant", "content": "hello"}],
    )
    ctx = ctx.model_copy(
        update={
            "state": third.state,
            "history": (*ctx.history, to_step_result(uuid4(), tool, workspace)),
        }
    )
    done = invoke(agent.advance, ctx, to_step_result(uuid4(), final, workspace))
    assert isinstance(done, Done)
    assert done.output == "hello"
    followup = invoke(
        agent.start,
        ctx.model_copy(
            update={
                "state": done.state,
                "input": "Next",
                "history": (*ctx.history, to_step_result(uuid4(), final, workspace)),
            }
        ),
    )
    assert followup.step.context[: len(third.step.context)] == third.step.context
    assert followup.step.context[-1] == {"role": "user", "content": "Next"}
