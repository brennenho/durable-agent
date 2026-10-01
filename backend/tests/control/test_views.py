import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.contracts import ModelResult, SubmitRunRequest, ToolResult
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
        return Operation(result=result.model_dump(mode="json"), completed_at=datetime.now(UTC))

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
