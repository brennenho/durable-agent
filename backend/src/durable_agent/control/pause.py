"""Pause scheduling without changing saved progress or the current attempt."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.control.errors import ControlError
from durable_agent.db.models import Operation, Run


async def set_pause(session: AsyncSession, run_id: UUID, paused: bool) -> None:
    run = await session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ControlError(404, "run_not_found", "Run not found")
    if run.parent_operation_id is not None:
        raise ControlError(
            409, "child_run_read_only", "Control this subagent through its parent run"
        )
    # Claims and reports take the same lock; an assigned call can finish normally.
    run.pause_requested = paused and run.status not in ("succeeded", "failed")
    # Root before children is the shared lock order. Child claims/reports lock only the child.
    children = await session.scalars(
        select(Run)
        .join(Operation, Run.parent_operation_id == Operation.id)
        .where(Operation.run_id == run.id, Run.status.not_in(("succeeded", "failed")))
        .order_by(Run.child_index)
        .with_for_update(of=Run)
        .execution_options(populate_existing=True)
    )
    for child in children:
        child.pause_requested = run.pause_requested
    await session.flush()
