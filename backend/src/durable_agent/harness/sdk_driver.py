"""Invoke pure callbacks and freeze their next step for durable execution."""

from collections.abc import Callable
from uuid import UUID

from pydantic import TypeAdapter

from durable_agent.contracts import (
    AgentConfig,
    ModelOperation,
    ModelRequest,
    Operation,
    ToolOperation,
    ToolRequest,
)
from durable_agent.contracts import ToolCall as WireToolCall
from durable_agent.harness import ARGUMENTS
from durable_agent.sdk import AgentContext, ChildBatch, Decision, Fail, ModelCall, Step, StepResult

DECISION = TypeAdapter(Decision)


def invoke(callback: Callable, context: AgentContext, result: StepResult | None = None) -> Decision:
    """Callbacks receive copies; only a validated decision crosses back into persistence."""
    try:
        args = (context.model_copy(deep=True),)
        if result is not None:
            args += (result.model_copy(deep=True),)
        decision = DECISION.validate_python(callback(*args))
        return DECISION.validate_json(DECISION.dump_json(decision))
    except Exception as exc:
        return Fail(code="agent_callback_failed", message=f"{type(exc).__name__}: {exc}")


def plan_step(
    step: Step,
    config: AgentConfig,
    operation_id: UUID,
    workspace_version_id: UUID,
    *,
    steps_taken: int,
    can_delegate: bool,
) -> Operation:
    if steps_taken >= config.max_steps:
        raise ValueError("Agent step limit reached")
    schemas = {schema["name"]: schema for schema in config.tool_schemas}
    if isinstance(step, ModelCall):
        if len(step.tools) != len(set(step.tools)) or any(
            name not in schemas for name in step.tools
        ):
            raise ValueError("Model tools must name unique registered tools")
        items = [*step.context]
        if step.prompt:
            items.append({"role": "user", "content": step.prompt})
        return ModelOperation(
            kind="model",
            request=ModelRequest(
                prompt=step.prompt,
                history=[],
                explicit_input=items,
                model=step.model,
                model_params=step.parameters,
                tool_schemas=[schemas[name] for name in step.tools],
                timeout_seconds=step.timeout_seconds,
            ),
        )
    if isinstance(step, ChildBatch):
        name, call_id, timeout = "delegate", str(operation_id), None
        arguments = {
            "tasks": [dict(task.model_dump(mode="json"), prompt=task.name) for task in step.tasks]
        }
    else:
        name, call_id, timeout = step.name, step.call_id or str(operation_id), step.timeout_seconds
        arguments = step.arguments
    if name == "delegate":
        if not can_delegate:
            raise ValueError("Only one child batch is allowed, and children cannot delegate")
    elif name not in schemas:
        raise ValueError(f"Tool is not registered: {name}")
    if validator := ARGUMENTS.get(name):
        validator.model_validate(arguments, strict=True)
    return ToolOperation(
        kind="tool",
        request=ToolRequest(
            call=WireToolCall(call_id=call_id, name=name, arguments=arguments),
            input_version_id=workspace_version_id,
            timeout_seconds=timeout,
        ),
    )
