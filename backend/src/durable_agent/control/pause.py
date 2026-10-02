"""Pause scheduling without changing saved progress or the current attempt."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.control.errors import ControlError
from durable_agent.db.models import Run


async def set_pause(session: AsyncSession, run_id: UUID, paused: bool) -> None:
    run = await session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ControlError(404, "run_not_found", "Run not found")
    # Claims and reports take the same lock; an assigned call can finish normally.
    run.pause_requested = paused and run.status not in ("succeeded", "failed")
    await session.flush()
