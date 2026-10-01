import asyncio
from uuid import uuid4

import pytest
from durable_agent.contracts import FailedOutcome, ModelResult, SubmitFollowUpRequest, ToolResult
from durable_agent.control.continuations import create_continuation
from durable_agent.control.errors import ControlError
from durable_agent.db.models import Operation, Run
from sqlalchemy import func, select

from .test_leases import claimed, queued
from .test_reports import report, report_body


async def submit(db, conversation_id, body, *, commit=True):
    async with db.session() as session:
        run = await create_continuation(session, conversation_id, body)
        if commit:
            await session.commit()
        return run


async def finish(db, result=None):
    assignment = await claimed(db)
    await report(db, assignment, await report_body(db, assignment, result))


def request(after_run_id):
    return SubmitFollowUpRequest(
        submission_key=uuid4(), prompt="Continue", after_run_id=after_run_id
    )


def test_follow_ups_save_complete_context_atomically_and_replay_after_later_turns(database, config):
    async def exercise():
        async with database() as db:
            first_id = await queued(db, config)
            await finish(db)
            body = request(first_id)
            await submit(db, first_id, body, commit=False)
            async with db.session() as session:
                assert await session.scalar(select(func.count()).select_from(Run)) == 1
                assert await session.scalar(select(func.count()).select_from(Operation)) == 1
            second = await submit(db, first_id, body)
            assert second.turn_index == 1
            assert second.config == config.model_dump(mode="json")
            assert (await submit(db, first_id, body)).id == second.id
            await finish(db)
            third = await submit(db, first_id, request(second.id))
            assert third.turn_index == 2
            assert (await submit(db, first_id, body)).id == second.id
            assignment = await claimed(db)
            assert assignment.attempt_no == assignment.lease_generation == 1
            saved = assignment.operation.request
            assert saved.history == []
            assert [turn.prompt for turn in saved.previous_turns] == ["Hi", "Continue"]
            assert all(turn.history[0].output.text == "Done" for turn in saved.previous_turns)
            assert all(turn.terminal_error is None for turn in saved.previous_turns)

    asyncio.run(exercise())


def test_failed_turn_inherits_only_accepted_files_and_keeps_unanswered_calls(database, config):
    async def exercise():
        async with database() as db:
            config.max_turns = 1
            first_id = await queued(db, config)
            planned = ModelResult(
                kind="model",
                native_context=[{"opaque": "provider state"}],
                output={
                    "status": "completed",
                    "text": "",
                    "tool_calls": [
                        {
                            "call_id": key,
                            "name": "write_file",
                            "arguments": {"path": key, "content": key},
                        }
                        for key in ["saved", "interrupted"]
                    ],
                },
            )
            await finish(db, planned)
            accepted = ToolResult(
                kind="tool",
                call_id="saved",
                output={"status": "ok", "value": {}},
                workspace_version_id=uuid4(),
            )
            await finish(db, accepted)
            interrupted = await claimed(db)
            unsaved = accepted.model_copy(
                update={"call_id": "interrupted", "workspace_version_id": uuid4()}
            )
            body = await report_body(db, interrupted, unsaved)
            await report(db, interrupted, body, commit=False)
            body.outcome = FailedOutcome(
                status="failed",
                error={
                    "code": "sandbox_failed",
                    "message": "No saved result",
                    "retryable": False,
                },
            )
            await report(db, interrupted, body)
            follow_up = await submit(db, first_id, request(first_id))
            assert follow_up.config["initial_workspace_version_id"] == str(
                accepted.workspace_version_id
            )
            assignment = await claimed(db)
            assert assignment.attempt_no == assignment.lease_generation == 1
            assert assignment.config.max_turns == 1
            saved = assignment.operation.request
            assert saved.history == []
            assert saved.previous_turns[0].history == [planned, accepted]
            assert saved.previous_turns[0].terminal_error.code == "sandbox_failed"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "case,code",
    [
        ("busy", "conversation_busy"),
        ("stale", "stale_conversation"),
        ("unknown", "conversation_not_found"),
        ("prompt", "submission_conflict"),
        ("predecessor", "submission_conflict"),
        ("conversation", "submission_conflict"),
        ("initial_key", "submission_conflict"),
    ],
)
def test_invalid_submissions_do_not_add_turns(database, config, case, code):
    async def exercise():
        async with database() as db:
            first_id = await queued(db, config)
            if case != "busy":
                await finish(db)
            conversation_id, body, count = first_id, request(first_id), 1
            if case in ("prompt", "predecessor", "conversation"):
                await submit(db, first_id, body)
                count = 2
            if case == "prompt":
                body.prompt = "Changed"
            if case in ("predecessor", "stale"):
                body.after_run_id = uuid4()
            if case in ("conversation", "unknown"):
                conversation_id = uuid4()
            if case == "initial_key":
                async with db.session() as session:
                    body.submission_key = (await session.get(Run, first_id)).submission_key
            with pytest.raises(ControlError) as caught:
                await submit(db, conversation_id, body)
            assert caught.value.error.code == code
            assert caught.value.status == (404 if case == "unknown" else 409)
            async with db.session() as session:
                assert await session.scalar(select(func.count()).select_from(Run)) == count

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "same_key,same_conversation,error",
    [
        (True, True, None),
        (False, True, "stale_conversation"),
        (True, False, "submission_conflict"),
    ],
)
def test_concurrent_follow_ups_create_one_turn(
    database, config, same_key, same_conversation, error
):
    async def exercise():
        async with database() as db:
            first_id = await queued(db, config)
            await finish(db)
            other_id = first_id
            if not same_conversation:
                other_id = await queued(db, config)
                await finish(db)
            body = request(first_id)
            other = body.model_copy(
                update={
                    "after_run_id": other_id,
                    "submission_key": body.submission_key if same_key else uuid4(),
                }
            )

            async def attempt(conversation_id, submitted):
                try:
                    return (await submit(db, conversation_id, submitted)).id
                except ControlError as exc:
                    return exc.error.code

            results = await asyncio.gather(attempt(first_id, body), attempt(other_id, other))
            if error is None:
                assert results[0] == results[1]
            else:
                assert results.count(error) == 1
            async with db.session() as session:
                expected = 2 if same_conversation else 3
                for table in (Run, Operation):
                    assert await session.scalar(select(func.count()).select_from(table)) == expected

    asyncio.run(exercise())
