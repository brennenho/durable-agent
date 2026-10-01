"""Recover expired leases in bounded batches; callers commit explicitly."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import ExecutionError
from durable_agent.control.reports import failed_attempt
from durable_agent.db.models import Attempt, Operation, Run


async def recover_expired(session: AsyncSession, limit: int = 100) -> int:
    runs = await session.scalars(
        select(Run)
        .where(Run.status == "running", Run.lease_expires_at <= func.clock_timestamp())
        .order_by(Run.lease_expires_at, Run.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    recovered = 0
    for run in runs:
        attempt = await session.scalar(
            select(Attempt)
            .join(Operation)
            .where(
                Operation.run_id == run.id,
                Attempt.status == "running",
                Attempt.lease_generation == run.lease_generation,
            )
        )
        if attempt is None:
            raise ValueError("Running run has no current attempt")
        now = await session.scalar(select(func.clock_timestamp()))
        failed_attempt(
            run,
            attempt,
            ExecutionError(
                code="lease_expired",
                message="Worker lease expired before a report was saved",
                retryable=True,
            ),
            now,
        )
        attempt.status = "unknown"
        recovered += 1
    await session.flush()
    return recovered
