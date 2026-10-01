import asyncio

import pytest
from durable_agent.control.errors import ControlError
from durable_agent.control.recovery import recover_expired
from durable_agent.control.views import detail
from durable_agent.db.models import Run
from sqlalchemy import func, text, update

from .test_leases import claimed, queued
from .test_reports import report, report_body


async def expire(db):
    async with db.session() as session:
        await session.execute(
            update(Run)
            .where(Run.status == "running")
            .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )
        await session.commit()


async def recover(db, commit=True):
    async with db.session() as session:
        count = await recover_expired(session)
        if commit:
            await session.commit()
        return count


def test_recovery_preserves_operation_and_rejects_late_results(database, config):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            first = await claimed(db)
            body = await report_body(db, first)
            assert await recover(db) == 0
            await expire(db)
            assert await recover(db, commit=False) == 1
            async with db.session() as session:
                assert (await detail(session, run_id)).status == "running"
            counts = await asyncio.gather(recover(db), recover(db))
            assert sum(counts) == 1
            assert await claimed(db) is None  # Retry delay is respected.
            async with db.session() as session:
                await session.execute(update(Run).values(next_attempt_at=func.clock_timestamp()))
                await session.commit()
            second = await claimed(db)
            assert second.operation_id == first.operation_id
            assert second.operation == first.operation
            assert second.attempt_no == first.attempt_no + 1
            assert second.lease_generation == first.lease_generation + 1
            with pytest.raises(ControlError) as caught:
                await report(db, first, body)
            assert caught.value.status == 409
            async with db.session() as session:
                saved = await detail(session, run_id)
                assert saved.operations[0].attempts[0].status == "unknown"
                assert saved.operations[0].result is None

    asyncio.run(exercise())


def test_last_expired_attempt_fails_run(database, config):
    async def exercise():
        async with database() as db:
            config.max_attempts = 1
            run_id = await queued(db, config)
            await claimed(db)
            await expire(db)
            assert await recover(db) == 1
            assert await recover(db) == 0
            assert await claimed(db) is None
            async with db.session() as session:
                saved = await detail(session, run_id)
                assert saved.status == "failed"
                assert saved.terminal_error.code == "lease_expired"
                assert saved.finished_at is not None

    asyncio.run(exercise())
