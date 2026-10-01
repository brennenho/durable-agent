"""Resolve file versions using Control's accepted progress, never tool tables."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.control.errors import ControlError
from durable_agent.db.models import Operation, Run


async def saved_version(session: AsyncSession, run_id: UUID, requested: UUID | None = None) -> UUID:
    run = await session.get(Run, run_id)
    if run is None:
        raise ControlError(404, "run_not_found", "Run not found")
    seed = UUID(run.config["initial_workspace_version_id"])
    if requested == seed:
        return seed
    version = Operation.result["workspace_version_id"].astext
    query = select(version).where(
        Operation.run_id == run_id,
        Operation.kind == "tool",
        Operation.completed_at.is_not(None),
    )
    if requested is not None:
        query = query.where(version == str(requested))
    accepted = await session.scalar(query.order_by(Operation.position.desc()).limit(1))
    if accepted is not None:
        return UUID(accepted)
    if requested is not None:
        raise ControlError(404, "version_not_found", "Workspace version not accepted for this run")
    return seed
