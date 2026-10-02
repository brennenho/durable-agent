import asyncio
from datetime import timedelta

import pytest
from durable_agent.contracts import FailedOutcome, ModelResult, ToolResult
from durable_agent.control.errors import ControlError
from durable_agent.control.leases import heartbeat
from durable_agent.control.views import detail
from durable_agent.db.models import Attempt, Run
from sqlalchemy import func, select, update

from .test_leases import claimed, queued
from .test_reports import report, report_body


async def plan_tool(db, config, name, arguments):
    config.tool_schemas = [{"name": name}]
    await queued(db, config)
    model = await claimed(db)
    result = ModelResult(
        kind="model",
        native_context=[],
        output={
            "status": "completed",
            "text": "",
            "tool_calls": [{"call_id": "tool", "name": name, "arguments": arguments}],
        },
    )
    await report(db, model, await report_body(db, model, result))
    return model, await claimed(db)


async def age_attempt(db, assignment, elapsed):
    async with db.session() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        started = now - timedelta(seconds=elapsed)
        await session.execute(
            update(Attempt).where(Attempt.id == assignment.attempt_id).values(started_at=started)
        )
        # Keep the lease live to test the independent fixed deadline.
        await session.execute(
            update(Run)
            .where(Run.id == assignment.run_id)
            .values(lease_expires_at=now + timedelta(seconds=30))
        )
        await session.commit()
        return started


def result_for(assignment):
    return ToolResult(
        kind="tool",
        call_id="tool",
        output={"status": "ok", "value": {}},
        workspace_version_id=assignment.operation.request.input_version_id,
    )


@pytest.mark.parametrize(
    "name,arguments,budget",
    [
        ("run_command", {"command": "pytest"}, 150),
        ("run_command", {"command": "pytest", "timeout_seconds": 300}, 330),
        ("run_command", {"command": "pytest", "timeout_seconds": 900}, 930),
        ("read_file", {"path": "a.py"}, 7),
    ],
)
def test_claim_and_heartbeats_share_fixed_operation_budget(
    database, config, name, arguments, budget
):
    async def exercise():
        async with database() as db:
            config.call_timeout_seconds = 7
            model, tool = await plan_tool(db, config, name, arguments)
            assert (model.deadline_at - model.server_time).total_seconds() == 7
            assert (tool.deadline_at - tool.server_time).total_seconds() == budget
            assert tool.operation.request.call.arguments == arguments
            body = await report_body(db, tool, result_for(tool))
            for _ in range(2):
                async with db.session() as session:
                    renewed = await heartbeat(session, tool.attempt_id, body)
                    await session.commit()
                assert renewed.deadline_at == tool.deadline_at
                assert renewed.lease_expires_at <= tool.deadline_at

    asyncio.run(exercise())


@pytest.mark.parametrize("expired", [False, True])
def test_reports_and_heartbeats_enforce_command_deadline(database, config, expired):
    async def exercise():
        async with database() as db:
            _, tool = await plan_tool(
                db, config, "run_command", {"command": "pytest", "timeout_seconds": 300}
            )
            started = await age_attempt(db, tool, 331 if expired else 310)
            timed_out = ToolResult(
                kind="tool",
                call_id="tool",
                output={"status": "error", "code": "command_timeout", "message": "Stopped"},
                workspace_version_id=tool.operation.request.input_version_id,
            )
            body = await report_body(db, tool, timed_out)
            if expired:
                async with db.session() as session:
                    with pytest.raises(ControlError, match="expired"):
                        await heartbeat(session, tool.attempt_id, body)
                with pytest.raises(ControlError, match="expired"):
                    await report(db, tool, body)
                async with db.session() as session:
                    assert (await detail(session, tool.run_id)).operations[-1].result is None
            else:
                async with db.session() as session:
                    renewed = await heartbeat(session, tool.attempt_id, body)
                    await session.commit()
                assert renewed.deadline_at == started + timedelta(seconds=330)
                assert renewed.lease_expires_at == renewed.deadline_at
                assert await report(db, tool, body) == "recorded"
                assert await report(db, tool, body) == "already_recorded"
                async with db.session() as session:
                    saved = await detail(session, tool.run_id)
                assert saved.status == "queued"
                assert saved.operations[-2].attempts[0].status == "accepted"
                assert saved.operations[-2].result == timed_out
                assert saved.operations[-1].kind == "model"
                assert saved.workspace_version_id == tool.operation.request.input_version_id

    asyncio.run(exercise())


def test_retry_keeps_command_inputs_and_gets_a_fresh_bounded_deadline(database, config):
    async def exercise():
        async with database() as db:
            _, first = await plan_tool(
                db, config, "run_command", {"command": "pytest", "timeout_seconds": 300}
            )
            await age_attempt(db, first, 200)
            body = await report_body(db, first, result_for(first))
            body.outcome = FailedOutcome(
                status="failed",
                error={
                    "code": "sandbox_unavailable",
                    "message": "Try again",
                    "retryable": True,
                    "retry_after_seconds": 0,
                },
            )
            assert await report(db, first, body) == "recorded"
            second = await claimed(db)
            assert second.operation_id == first.operation_id
            assert second.operation == first.operation
            assert second.attempt_no == first.attempt_no + 1
            assert second.lease_generation > first.lease_generation
            assert second.deadline_at > first.deadline_at
            assert (second.deadline_at - second.server_time).total_seconds() == 330
            async with db.session() as session:
                with pytest.raises(ControlError):
                    await heartbeat(session, first.attempt_id, body)

    asyncio.run(exercise())
