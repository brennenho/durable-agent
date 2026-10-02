import asyncio
from uuid import uuid4

import pytest
from durable_agent.contracts import ModelResult, ToolResult
from durable_agent.control.errors import ControlError
from durable_agent.control.progress import accept_result
from durable_agent.control.views import detail
from durable_agent.db.models import Attempt, Operation, Run
from sqlalchemy import func, select

from .test_leases import claimed, queued
from .test_reports import report, report_body


def test_control_result_commits_with_next_step_without_worker_attempt(database, config):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            model = await claimed(db)
            plan = ModelResult(
                kind="model",
                output={
                    "status": "completed",
                    "text": "",
                    "tool_calls": [
                        {"call_id": "read", "name": "read_file", "arguments": {"path": "a"}}
                    ],
                },
                native_context=[],
            )
            await report(db, model, await report_body(db, model, plan))
            version = uuid4()
            result = ToolResult(
                kind="tool",
                call_id="read",
                output={"status": "ok", "value": {"content": "saved"}},
                workspace_version_id=version,
            )
            for commit in [False, True]:
                async with db.session() as session:
                    run = await session.scalar(
                        select(Run).where(Run.id == run_id).with_for_update()
                    )
                    operation = await session.scalar(
                        select(Operation).where(Operation.run_id == run_id, Operation.position == 1)
                    )
                    now = await session.scalar(select(func.clock_timestamp()))
                    await accept_result(session, run, operation, result, now)
                    await session.flush()
                    if commit:
                        await session.commit()
                async with db.session() as session:
                    saved = await detail(session, run_id)
                    assert len(saved.operations) == (3 if commit else 2)
                    assert saved.operations[1].result == (result if commit else None)
                    assert saved.workspace_version_id == (
                        version if commit else config.initial_workspace_version_id
                    )
                    assert await session.scalar(select(func.count()).select_from(Attempt)) == 1
            async with db.session() as session:
                run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
                operation = await session.scalar(
                    select(Operation).where(Operation.run_id == run_id, Operation.position == 1)
                )
                with pytest.raises(ControlError, match="already has an accepted result"):
                    await accept_result(session, run, operation, result, now)

    asyncio.run(exercise())
