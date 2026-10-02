"""Count useful parallel work and delay scale-down through brief gaps between calls."""

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.db.models import Run


async def worker_demand(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(Run)
        .where(
            or_(
                and_(
                    Run.status.in_(["queued", "retrying"]),
                    Run.pause_requested.is_(False),
                    Run.next_attempt_at <= func.clock_timestamp(),
                ),
                # A pause request does not interrupt an already running assignment.
                and_(Run.status == "running", Run.lease_expires_at > func.clock_timestamp()),
            )
        )
    )


@dataclass
class CapacityPolicy:
    maximum: int
    down_delay: float = 60
    underused_since: float | None = None

    def desired(self, demand: int, current: int, now: float) -> int:
        target = max(2, min(self.maximum, demand))
        if target >= current:
            self.underused_since = None
            return target
        if self.underused_since is None:
            self.underused_since = now
        if now - self.underused_since >= self.down_delay:
            self.underused_since = None
            return target
        return min(current, self.maximum)
