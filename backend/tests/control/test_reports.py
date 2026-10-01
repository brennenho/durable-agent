import asyncio
from uuid import uuid4

import pytest
from durable_agent.contracts import FailedOutcome, ModelResult, ReportRequest, ToolResult
from durable_agent.control.errors import ControlError
from durable_agent.control.reports import accept_report
from durable_agent.control.views import detail
from durable_agent.db.models import Attempt, Operation, Run
from sqlalchemy import func, select, text, update

from .test_leases import claimed, queued


async def report_body(db, assignment, result=None):
    async with db.session() as session:
        attempt = await session.get(Attempt, assignment.attempt_id)
        return ReportRequest(
            worker_id=attempt.worker_id,
            lease_generation=attempt.lease_generation,
            outcome={
                "status": "succeeded",
                "result": result
                or ModelResult(
                    kind="model",
                    output={"status": "completed", "text": "Done", "tool_calls": []},
                    native_context=[],
                ),
            },
        )


async def report(db, assignment, body, commit=True):
    async with db.session() as session:
        result = await accept_report(session, assignment.attempt_id, body)
        if commit:
            await session.commit()
        return result.status


def test_concurrent_reports_and_lost_ack_advance_once(database, config):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            assigned = await claimed(db)
            body = await report_body(db, assigned)
            results = await asyncio.gather(report(db, assigned, body), report(db, assigned, body))
            assert sorted(results) == ["already_recorded", "recorded"]
            assert await report(db, assigned, body) == "already_recorded"
            body.outcome.result.output.text = "Changed"
            with pytest.raises(ControlError) as caught:
                await report(db, assigned, body)
            assert caught.value.status == 409
            async with db.session() as session:
                saved = await detail(session, run_id)
                assert saved.status == "succeeded"
                assert saved.final_answer == "Done"
                assert len(saved.operations) == 1

    asyncio.run(exercise())


def test_tool_plan_and_workspace_advance_atomically(database, config):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            assigned = await claimed(db)
            result = ModelResult(
                kind="model",
                output={
                    "status": "completed",
                    "text": "",
                    "tool_calls": [
                        {
                            "call_id": "a",
                            "name": "write_file",
                            "arguments": {"path": "a", "content": "Hi"},
                        },
                        {"call_id": "b", "name": "read_file", "arguments": {"path": "a"}},
                    ],
                },
                native_context=[],
            )
            body = await report_body(db, assigned, result)
            await report(db, assigned, body, commit=False)
            async with db.session() as session:
                saved = await detail(session, run_id)
                assert saved.status == "running"
                assert saved.operations[0].result is None
                assert len(saved.operations) == 1
            await report(db, assigned, body)
            tool = await claimed(db)
            assert tool.operation.request.input_version_id == config.initial_workspace_version_id
            assert tool.operation.request.call.call_id == "a"
            version = uuid4()
            tool_body = await report_body(
                db,
                tool,
                ToolResult(
                    kind="tool",
                    call_id="a",
                    output={"status": "ok", "value": {}},
                    workspace_version_id=version,
                ),
            )
            await report(db, tool, tool_body)
            next_tool = await claimed(db)
            assert next_tool.operation.request.call.call_id == "b"
            assert next_tool.operation.request.input_version_id == version
            assert await report(db, assigned, body) == "already_recorded"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "retryable,max_attempts,status",
    [(True, 5, "retrying"), (False, 5, "failed"), (True, 1, "failed")],
)
def test_failed_reports_schedule_retry_or_fail_without_advancing(
    database, config, retryable, max_attempts, status
):
    async def exercise():
        async with database() as db:
            config.max_attempts = max_attempts
            run_id = await queued(db, config)
            assigned = await claimed(db)
            body = await report_body(db, assigned)
            body = body.model_copy(
                update={
                    "outcome": FailedOutcome(
                        status="failed",
                        error={
                            "code": "timeout",
                            "message": "Timed out",
                            "retryable": retryable,
                            "retry_after_seconds": 7,
                        },
                    )
                }
            )
            await report(db, assigned, body)
            assert await report(db, assigned, body) == "already_recorded"
            async with db.session() as session:
                run = await session.get(Run, run_id)
                attempt = await session.get(Attempt, assigned.attempt_id)
                assert run.status == status
                if status == "retrying":
                    assert (run.next_attempt_at - attempt.finished_at).total_seconds() == 7
                assert await session.scalar(select(func.count()).select_from(Operation)) == 1

    asyncio.run(exercise())


def test_expired_report_cannot_publish(database, config):
    async def exercise():
        async with database() as db:
            await queued(db, config)
            assigned = await claimed(db)
            body = await report_body(db, assigned)
            async with db.session() as session:
                await session.execute(
                    update(Run).values(
                        lease_expires_at=func.clock_timestamp() - text("interval '1 second'")
                    )
                )
                await session.commit()
            with pytest.raises(ControlError) as caught:
                await report(db, assigned, body)
            assert caught.value.status == 409

    asyncio.run(exercise())


def test_wrong_result_kind_cannot_change_progress(database, config):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            assigned = await claimed(db)
            body = await report_body(
                db,
                assigned,
                ToolResult(
                    kind="tool",
                    call_id="unplanned",
                    output={"status": "ok", "value": {}},
                    workspace_version_id=uuid4(),
                ),
            )
            with pytest.raises(ControlError) as caught:
                await report(db, assigned, body)
            assert caught.value.error.code == "invalid_result"
            async with db.session() as session:
                saved = await detail(session, run_id)
                assert saved.status == "running"
                assert saved.operations[0].result is None

    asyncio.run(exercise())
