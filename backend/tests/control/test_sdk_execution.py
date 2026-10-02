import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from durable_agent.contracts import HeartbeatRequest, ReportRequest, ToolResult
from durable_agent.control.agent_progress import start_agent
from durable_agent.control.history import load_operations
from durable_agent.control.leases import claim, heartbeat
from durable_agent.control.reports import accept_report
from durable_agent.db.models import Attempt, Run
from sqlalchemy import func, select

from .test_agent_progress import RUNTIME, added


def test_sdk_reports_are_deduplicated_before_callbacks(database, config):
    async def exercise():
        async with database() as db:
            async with db.session() as session:
                run = await added(session, config, "loop")
                await start_agent(session, run, RUNTIME, datetime.now(UTC))
                await session.commit()
                run_id = run.id
            async with db.session() as session:
                assigned = await claim(session, worker := uuid4(), agents=RUNTIME)
                await session.commit()
            body = ReportRequest(
                worker_id=worker,
                lease_generation=assigned.lease_generation,
                outcome={
                    "status": "succeeded",
                    "result": ToolResult(
                        kind="tool",
                        call_id=assigned.operation.request.call.call_id,
                        output={"status": "ok", "value": {}},
                        workspace_version_id=uuid4(),
                    ),
                },
            )

            async def report():
                async with db.session() as session:
                    response = await accept_report(
                        session, assigned.attempt_id, body, agents=RUNTIME
                    )
                    await session.commit()
                    return response.status

            assert sorted(await asyncio.gather(report(), report())) == [
                "already_recorded",
                "recorded",
            ]
            assert await report() == "already_recorded"
            async with db.session() as session:
                run = await session.get(Run, run_id)
                assert run.agent_state == 1
                assert len(await load_operations(session, run_id)) == 2

    asyncio.run(exercise())


def test_incompatible_saved_agent_is_not_assigned(database, config):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config, "loop")
            await start_agent(session, run, RUNTIME, datetime.now(UTC))
            await session.flush()
            assert await claim(session, uuid4()) is None
            await session.commit()
            assert run.status == "failed"
            assert run.terminal_error["code"] == "agent_incompatible"
            assert await session.scalar(select(func.count()).select_from(Attempt)) == 0

    asyncio.run(exercise())


def test_explicit_step_deadline_is_fixed_across_heartbeats(database, config):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config, "loop")
            await start_agent(session, run, RUNTIME, datetime.now(UTC))
            await session.flush()
            operation = (await load_operations(session, run.id))[0]
            operation.request = {**operation.request, "timeout_seconds": 7}
            await session.flush()
            assigned = await claim(session, worker := uuid4(), agents=RUNTIME)
            await session.flush()
            renewed = await heartbeat(
                session,
                assigned.attempt_id,
                HeartbeatRequest(worker_id=worker, lease_generation=assigned.lease_generation),
            )
            assert (assigned.deadline_at - assigned.server_time).total_seconds() == 7
            assert renewed.deadline_at == assigned.deadline_at

    asyncio.run(exercise())
