import asyncio

import pytest
from durable_agent.control.capacity import CapacityPolicy, worker_demand
from durable_agent.control.pause import set_pause
from durable_agent.db.models import Run
from sqlalchemy import func, text, update

from .test_leases import claimed, queued
from .test_recovery import expire, recover
from .test_reports import report, report_body


@pytest.mark.parametrize("demand,expected", [(0, 2), (1, 2), (2, 2), (3, 3), (4, 4), (50, 4)])
def test_scale_up_preserves_minimum_and_cap(demand, expected):
    assert CapacityPolicy(4).desired(demand, current=2, now=0) == expected


def test_scale_down_requires_sustained_lower_demand():
    policy = CapacityPolicy(4)
    assert policy.desired(0, 4, 0) == 4
    assert policy.desired(0, 4, 59) == 4
    assert policy.desired(4, 4, 59.5) == 4  # Demand resets the waiting period.
    assert policy.desired(0, 4, 60) == 4
    assert policy.desired(3, 4, 120) == 3
    assert policy.desired(0, 3, 121) == 3
    assert policy.desired(0, 3, 181) == 2
    assert policy.desired(10, 2, 182) == 4


def test_control_restart_waits_before_reducing_existing_capacity():
    assert CapacityPolicy(4).desired(0, current=4, now=500) == 4


def test_demand_matches_claim_eligibility_and_active_leases(database, config):
    async def demand(db):
        async with db.session() as session:
            return await worker_demand(session)

    async def exercise():
        async with database() as db:
            assert await demand(db) == 0
            run_id = await queued(db, config)
            assert await demand(db) == 1
            async with db.session() as session:
                await set_pause(session, run_id, True)
                await session.commit()
            assert await demand(db) == 0
            async with db.session() as session:
                await set_pause(session, run_id, False)
                await session.commit()
            assignment = await claimed(db)
            assert await demand(db) == 1
            async with db.session() as session:
                await set_pause(session, run_id, True)
                await session.commit()
            assert await demand(db) == 1  # Still uses a worker until its call ends.
            await expire(db)
            assert await demand(db) == 0
            await recover(db)
            async with db.session() as session:
                await set_pause(session, run_id, False)
                await session.commit()
            assert await demand(db) == 0  # Retry backoff has not elapsed.
            async with db.session() as session:
                await session.execute(
                    update(Run).values(
                        next_attempt_at=func.clock_timestamp() - text("interval '1 second'")
                    )
                )
                await session.commit()
            assert await demand(db) == 1
            assignment = await claimed(db)
            await report(db, assignment, await report_body(db, assignment))
            assert await demand(db) == 0

    asyncio.run(exercise())
