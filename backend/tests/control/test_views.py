import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.contracts import (
    ModelRequest,
    ModelResult,
    PreviousTurn,
    SubmitRunRequest,
    ToolResult,
)
from durable_agent.control.errors import ControlError
from durable_agent.control.runs import create_run
from durable_agent.control.views import (
    detail,
    list_summaries,
    load_operations,
    restore_state,
    workspace_version,
)
from durable_agent.db.models import Attempt, Operation, Run

from .test_leases import claimed, queued
from .test_reports import report, report_body


def model(*calls):
    return ModelResult(
        kind="model",
        output={"status": "completed", "text": "Done", "tool_calls": list(calls)},
        native_context=[{"opaque": "saved"}],
    )


def test_history_restores_pending_tools_and_accepted_workspace(config):
    first = {"call_id": "a", "name": "read_file", "arguments": {"path": "a"}}
    second = {**first, "call_id": "b"}
    edited_version = uuid4()
    run = Run(config=config.model_dump(mode="json"), prompt="Hi")

    def saved(result):
        return Operation(
            request={"prompt": "Hi", "history": []},
            result=result.model_dump(mode="json"),
            completed_at=datetime.now(UTC),
        )

    operations = [saved(model(first, second))]
    state = restore_state(run, operations)
    assert [call.call_id for call in state.pending_tool_calls] == ["a", "b"]
    assert workspace_version(state) == config.initial_workspace_version_id
    operations.append(
        saved(
            ToolResult(
                kind="tool",
                call_id="a",
                output={"status": "error", "code": "not_found", "message": "Missing"},
                workspace_version_id=edited_version,
            )
        )
    )
    operations.append(Operation(result=None, completed_at=None))
    state = restore_state(run, operations)
    assert state.turn_count == 1
    assert state.pending_tool_calls[0].call_id == "b"
    assert workspace_version(state) == edited_version
    assert state.history[0].native_context == [{"opaque": "saved"}]


def test_restoration_preserves_prior_turns_but_resets_execution_state(config):
    previous = PreviousTurn(
        prompt="Earlier request",
        history=[model({"call_id": "unfinished", "name": "read_file", "arguments": {"path": "a"}})],
        terminal_error={"code": "interrupted", "message": "No report", "retryable": False},
    )
    run = Run(config=config.model_dump(mode="json"), prompt="Continue")
    request = ModelRequest(prompt=run.prompt, history=[], previous_turns=[previous])
    operation = Operation(position=0, kind="model", request=request.model_dump(mode="json"))
    restored = restore_state(run, [operation])
    assert restored.previous_turns == [previous]
    assert restored.history == restored.pending_tool_calls == []
    assert restored.turn_count == 0
    assert workspace_version(restored) == config.initial_workspace_version_id


def test_detail_returns_ordered_attempts_and_final_answer(database, config):
    async def exercise():
        async with database() as db:
            async with db.session() as session:
                run = await create_run(
                    session, SubmitRunRequest(submission_key=uuid4(), prompt="Hi"), config
                )
                operation = (await load_operations(session, run.id))[0]
                now = datetime.now(UTC)
                operation.result = model().model_dump(mode="json")
                operation.completed_at = now
                run.status, run.finished_at, run.next_attempt_at = "succeeded", now, None
                for number, status in [(2, "accepted"), (1, "unknown")]:
                    session.add(
                        Attempt(
                            operation_id=operation.id,
                            attempt_no=number,
                            worker_id=uuid4(),
                            lease_generation=number,
                            status=status,
                            finished_at=now,
                        )
                    )
                await session.commit()
                run_id = run.id
            async with db.session() as session:
                result = await detail(session, run_id)
                assert result.final_answer == "Done"
                assert result.workspace_version_id == config.initial_workspace_version_id
                assert result.last_progress_at == now
                assert [attempt.attempt_no for attempt in result.operations[0].attempts] == [1, 2]
                assert result.operations[0].attempts[0].error is None
                assert (await list_summaries(session))[0].id == run_id
                with pytest.raises(ControlError) as caught:
                    await detail(session, uuid4())
                assert caught.value.status == 404

    asyncio.run(exercise())


@pytest.mark.parametrize("view", ["list", "detail"])
def test_views_refresh_rows_loaded_before_another_transaction_reports(database, config, view):
    async def exercise():
        async with database() as db:
            run_id = await queued(db, config)
            assignment = await claimed(db)
            body = await report_body(db, assignment)
            async with db.session() as session:
                run = await session.get(Run, run_id)
                operation = await session.get(Operation, assignment.operation_id)
                attempt = await session.get(Attempt, assignment.attempt_id)
                assert run.status == attempt.status == "running"
                assert operation.result is None
                await report(db, assignment, body)
                if view == "list":
                    listed = (await list_summaries(session))[0]
                    assert listed.status == "succeeded"
                    assert listed.last_progress_at is not None
                else:
                    saved = await detail(session, run_id)
                    assert saved.status == "succeeded"
                    assert saved.final_answer == "Done"
                    assert saved.operations[0].result == body.outcome.result
                    assert saved.operations[0].attempts[0].status == "accepted"

    asyncio.run(exercise())
