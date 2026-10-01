import asyncio
from uuid import uuid4

import pytest
from durable_agent.contracts import HeartbeatRequest, SubmitRunRequest
from durable_agent.control.errors import ControlError
from durable_agent.control.leases import claim, heartbeat
from durable_agent.control.runs import create_run
from durable_agent.db.models import Attempt, Run
from sqlalchemy import func, select, text, update


async def queued(db, config):
    async with db.session() as session:
        run = await create_run(
            session, SubmitRunRequest(submission_key=uuid4(), prompt="Hi"), config
        )
        await session.commit()
        return run.id


async def claimed(db):
    async with db.session() as session:
        assignment = await claim(session, uuid4())
        await session.commit()
        return assignment


def test_concurrent_claims_assign_one_attempt(database, config):
    async def exercise():
        async with database() as db:
            await queued(db, config)
            results = await asyncio.gather(claimed(db), claimed(db))
            assert results.count(None) == 1
            assigned = next(result for result in results if result is not None)
            assert assigned.attempt_no == assigned.lease_generation == 1
            assert (assigned.deadline_at - assigned.server_time).total_seconds() == 120
            assert (assigned.lease_expires_at - assigned.server_time).total_seconds() == 30
            async with db.session() as session:
                assert await session.scalar(select(func.count()).select_from(Attempt)) == 1

    asyncio.run(exercise())


def test_claim_rollback_leaves_work_available(database, config):
    async def exercise():
        async with database() as db:
            await queued(db, config)
            async with db.session() as session:
                abandoned = await claim(session, uuid4())
            retried = await claimed(db)
            assert retried.operation_id == abandoned.operation_id
            assert retried.attempt_no == retried.lease_generation == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("stale", ["owner", "generation", "expiry", "deadline", "unknown"])
def test_heartbeat_rejects_stale_attempts(database, config, stale):
    async def exercise():
        async with database() as db:
            await queued(db, config)
            assigned = await claimed(db)
            async with db.session() as session:
                attempt = await session.get(Attempt, assigned.attempt_id)
                body = HeartbeatRequest(
                    worker_id=attempt.worker_id, lease_generation=attempt.lease_generation
                )
                if stale == "owner":
                    body.worker_id = uuid4()
                if stale == "generation":
                    body.lease_generation += 1
                if stale == "expiry":
                    await session.execute(
                        update(Run).values(
                            lease_expires_at=func.clock_timestamp() - text("interval '1 second'")
                        )
                    )
                if stale == "deadline":
                    await session.execute(
                        update(Attempt).values(
                            started_at=func.clock_timestamp() - text("interval '121 seconds'")
                        )
                    )
                await session.commit()
            async with db.session() as session:
                with pytest.raises(ControlError) as caught:
                    await heartbeat(
                        session, uuid4() if stale == "unknown" else assigned.attempt_id, body
                    )
                assert caught.value.status == 409

    asyncio.run(exercise())


def test_heartbeat_cannot_extend_fixed_deadline(database, config):
    async def exercise():
        async with database() as db:
            config.call_timeout_seconds = 5
            await queued(db, config)
            assigned = await claimed(db)
            async with db.session() as session:
                attempt = await session.get(Attempt, assigned.attempt_id)
                renewed = await heartbeat(
                    session,
                    attempt.id,
                    HeartbeatRequest(worker_id=attempt.worker_id, lease_generation=1),
                )
                await session.commit()
                assert renewed.deadline_at == assigned.deadline_at
                assert renewed.lease_expires_at == assigned.deadline_at
                assert renewed.server_time >= assigned.server_time

    asyncio.run(exercise())
