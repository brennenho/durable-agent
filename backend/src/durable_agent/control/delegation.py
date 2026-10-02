"""Create isolated child runs under the parent's run lock."""

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import AgentConfig, DelegateArguments, ToolRequest
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.harness import start


async def create_children(session: AsyncSession, parent: Run, operation: Operation) -> bool:
    """Caller holds the parent lock and commits all children together."""
    existing = await session.scalar(select(Run.id).where(Run.parent_operation_id == operation.id))
    if existing is not None:
        return False
    request = ToolRequest.model_validate(operation.request)
    tasks = DelegateArguments.model_validate(request.call.arguments).tasks
    config = AgentConfig.model_validate(parent.config).model_copy(
        update={
            "initial_workspace_version_id": request.input_version_id,
            "tool_schemas": [s for s in parent.config["tool_schemas"] if s["name"] != "delegate"],
        }
    )
    now = await session.scalar(select(func.clock_timestamp()))
    for index, task in enumerate(tasks):
        transition = start(config, task.prompt)
        if transition.status != "continue":
            raise ValueError("Stored delegation has invalid child configuration")
        child_id = uuid4()
        session.add(Conversation(id=child_id))
        await session.flush()
        session.add(
            Run(
                id=child_id,
                conversation_id=child_id,
                submission_key=uuid4(),
                parent_operation_id=operation.id,
                child_index=index,
                prompt=task.prompt,
                config=config.model_dump(mode="json"),
                status="queued",
                pause_requested=parent.pause_requested,
                next_attempt_at=now,
            )
        )
        await session.flush()
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
