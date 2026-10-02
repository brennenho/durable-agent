"""Reconstruct callback context from accepted operation checkpoints."""

from durable_agent.contracts import AgentConfig
from durable_agent.control.history import RESULT
from durable_agent.db.models import Operation, Run
from durable_agent.harness.sdk_results import to_step_result
from durable_agent.sdk import AgentContext, Limits


def restore_context(run: Run, operations: list[Operation]) -> AgentContext:
    config = AgentConfig.model_validate(run.config)
    if config.agent is None:
        raise ValueError("SDK context requires a saved agent identity")
    version = config.initial_workspace_version_id
    history = list(config.agent_history)
    for operation in operations:
        if operation.completed_at is None:
            continue
        result = RESULT.validate_python(operation.result)
        if result.kind == "tool":
            version = result.workspace_version_id
        history.append(
            to_step_result(
                operation.id,
                result,
                version,
                tool_name=operation.request["call"]["name"] if operation.kind == "tool" else None,
            )
        )
    return AgentContext(
        run_id=run.id,
        agent=config.agent.name,
        version=config.agent.version,
        input=run.agent_input,
        state=run.agent_state,
        history=tuple(history),
        workspace_version_id=version,
        limits=Limits(
            max_steps=config.max_steps,
            max_attempts=config.max_attempts,
            call_timeout_seconds=config.call_timeout_seconds,
        ),
    )
