"""Create isolated child runs under the parent's run lock."""

from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import AgentConfig, DelegateArguments, ToolRequest
from durable_agent.control.agent_progress import start_agent
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.harness import start


async def create_children(
    session: AsyncSession, parent: Run, operation: Operation, *, agents: AgentRuntime | None = None
) -> bool:
    """Caller holds the parent lock and commits all children together."""
    existing = await session.scalar(select(Run.id).where(Run.parent_operation_id == operation.id))
    if existing is not None:
        return False
    request = ToolRequest.model_validate(operation.request)
    try:
        tasks = DelegateArguments.model_validate(request.call.arguments).tasks
    except ValidationError as exc:
        raise ControlError(409, "invalid_agent_step", "Invalid child batch") from exc
    config = AgentConfig.model_validate(parent.config).model_copy(
        update={
            "initial_workspace_version_id": request.input_version_id,
            "agent_history": [],
            "tool_schemas": [s for s in parent.config["tool_schemas"] if s["name"] != "delegate"],
        }
    )
    runtime = agents or AgentRuntime()
    if config.agent is not None:
        runtime.check(config)
        # Resolve the whole batch before inserting anything; unknown names fail the parent.
        configs = [
            runtime.freeze(config, task.agent if task.agent is not None else "builtin")
            for task in tasks
        ]
    else:
        configs = [config] * len(tasks)
    now = await session.scalar(select(func.clock_timestamp()))
    for index, (task, child_config) in enumerate(zip(tasks, configs, strict=True)):
        child_id = uuid4()
        session.add(Conversation(id=child_id))
        await session.flush()
        child = Run(
            id=child_id,
            conversation_id=child_id,
            submission_key=uuid4(),
            parent_operation_id=operation.id,
            child_index=index,
            prompt=task.prompt,
            config=child_config.model_dump(mode="json"),
            agent_input=(task.input if task.agent is not None else task.prompt)
            if child_config.agent is not None
            else None,
            status="queued",
            pause_requested=parent.pause_requested,
            next_attempt_at=now,
        )
        session.add(child)
        await session.flush()
        if child_config.agent is not None:
            await start_agent(session, child, runtime, now)
            continue
        transition = start(child_config, task.prompt)
        if transition.status != "continue":
            raise ValueError("Stored delegation has invalid child configuration")
        session.add(
            Operation(
                run_id=child_id,
                position=0,
                kind="model",
                request=transition.next.request.model_dump(mode="json"),
            )
        )
    parent.status, parent.next_attempt_at = "waiting", None
    await session.flush()
    return True
