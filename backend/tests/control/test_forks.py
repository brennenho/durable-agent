import asyncio
from uuid import uuid4

import pytest
from durable_agent.contracts import (
    FailedOutcome,
    SubmitFollowUpRequest,
    SubmitForkRequest,
    SubmitRunRequest,
    ToolResult,
)
from durable_agent.control.continuations import create_continuation
from durable_agent.control.conversations import conversation_detail
from durable_agent.control.errors import ControlError
from durable_agent.control.forks import create_fork
from durable_agent.control.runs import create_run
from durable_agent.control.views import detail, load_operations
from durable_agent.db.models import Conversation, Operation, Run
from sqlalchemy import func, select

from .test_leases import claimed, queued
from .test_reports import report, report_body
from .test_views import model


async def source_checkpoint(db, config):
    root = await queued(db, config)
    first = await claimed(db)
    await report(db, first, await report_body(db, first))
    async with db.session() as session:
        source = await create_continuation(
            session,
            root,
            SubmitFollowUpRequest(submission_key=uuid4(), prompt="Source", after_run_id=root),
        )
        await session.commit()
    plan = model(
        {"call_id": "edit", "name": "write_file", "arguments": {"path": "a", "content": "saved"}}
    )
    result = ToolResult(
        kind="tool",
        call_id="edit",
        output={"status": "ok", "value": {}},
        workspace_version_id=uuid4(),
    )
    for output in (plan, result):
        assignment = await claimed(db)
        await report(db, assignment, await report_body(db, assignment, output))
    body = SubmitForkRequest(
        submission_key=uuid4(), prompt="Branch", after_operation_id=assignment.operation_id
    )
    return source, body, [plan, result]


async def fork(db, source_id, body, *, commit=True):
    async with db.session() as session:
        run = await create_fork(session, source_id, body)
        if commit:
            await session.commit()
        return run


async def counts(db):
    async with db.session() as session:
        return [
            await session.scalar(select(func.count()).select_from(table))
            for table in (Conversation, Run, Operation)
        ]


@pytest.mark.parametrize("failed", [False, True])
def test_fork_is_atomic_and_copies_only_selected_context_and_files(database, config, failed):
    async def exercise():
        async with database() as db:
            source, body, prefix = await source_checkpoint(db, config)
            if failed:
                later = await claimed(db)
                outcome = await report_body(db, later)
                outcome.outcome = FailedOutcome(
                    status="failed",
                    error={
                        "code": "later_failure",
                        "message": "Do not inherit",
                        "retryable": False,
                    },
                )
                await report(db, later, outcome)
            async with db.session() as session:
                before = await detail(session, source.id)
            initial_counts = await counts(db)
            await fork(db, source.id, body, commit=False)
            assert await counts(db) == initial_counts
            created = await fork(db, source.id, body)
            assert created.id != source.id
            assert created.id == created.conversation_id
            assert created.turn_index == created.lease_generation == 0
            assert created.config == {
                **source.config,
                "initial_workspace_version_id": str(prefix[-1].workspace_version_id),
            }
            assert (await fork(db, source.id, body)).id == created.id
            assert await counts(db) == [n + 1 for n in initial_counts]
            async with db.session() as session:
                assert await detail(session, source.id) == before
                origin = (await conversation_detail(session, created.conversation_id)).forked_from
                assert origin.model_dump() == {
                    "conversation_id": source.conversation_id,
                    "run_id": source.id,
                    "operation_id": body.after_operation_id,
                    "position": 1,
                }
                saved = await detail(session, created.id)
                assert saved.status == "queued"
                assert len(saved.operations) == 1
                assert saved.operations[0].attempts == []
                request = (await load_operations(session, created.id))[0].request
                assert request["prompt"] == body.prompt and request["history"] == []
                assert [turn["prompt"] for turn in request["previous_turns"]] == ["Hi", "Source"]
                assert request["previous_turns"][-1]["history"] == [
                    r.model_dump(mode="json") for r in prefix
                ]
                assert request["previous_turns"][-1]["terminal_error"] is None

    asyncio.run(exercise())


def test_fork_conflicts_with_root_and_followup_keys_and_rejects_invalid_sources(database, config):
    async def exercise():
        async with database() as db:
            source, body, _ = await source_checkpoint(db, config)
            created = await fork(db, source.id, body)
            async with db.session() as session:
                operations = await load_operations(session, source.id)
            for source_id, operation_id, code in [
                (uuid4(), body.after_operation_id, "run_not_found"),
                (source.id, uuid4(), "operation_not_found"),
                (created.id, body.after_operation_id, "operation_not_found"),
                (source.id, operations[0].id, "checkpoint_not_available"),
                (source.id, operations[-1].id, "checkpoint_not_available"),
            ]:
                with pytest.raises(ControlError) as caught:
                    await fork(
                        db,
                        source_id,
                        body.model_copy(
                            update={"submission_key": uuid4(), "after_operation_id": operation_id}
                        ),
                    )
                assert caught.value.error.code == code
            for change in [
                {"prompt": "Changed"},
                {"after_operation_id": uuid4()},
                {"submission_key": source.submission_key},
            ]:
                with pytest.raises(ControlError, match="different inputs"):
                    await fork(db, source.id, body.model_copy(update=change))
            async with db.session() as session:
                with pytest.raises(ControlError, match="different inputs"):
                    await create_run(
                        session,
                        SubmitRunRequest(submission_key=body.submission_key, prompt=body.prompt),
                        config,
                    )
                with pytest.raises(ControlError, match="different inputs"):
                    await create_continuation(
                        session,
                        created.id,
                        SubmitFollowUpRequest(
                            submission_key=body.submission_key,
                            prompt=body.prompt,
                            after_run_id=created.id,
                        ),
                    )

    asyncio.run(exercise())


@pytest.mark.parametrize("conflict", [False, True])
def test_concurrent_forks_do_not_leave_duplicate_runs_or_empty_conversations(
    database, config, conflict
):
    async def exercise():
        async with database() as db:
            source, body, _ = await source_checkpoint(db, config)
            before = await counts(db)

            async def attempt(request):
                try:
                    return (await fork(db, source.id, request)).id
                except ControlError as exc:
                    return exc.error.code

            results = await asyncio.gather(
                attempt(body),
                attempt(body.model_copy(update={"prompt": "Other" if conflict else body.prompt})),
            )
            if conflict:
                assert results.count("submission_conflict") == 1
            else:
                assert results[0] == results[1]
            assert await counts(db) == [n + 1 for n in before]

    asyncio.run(exercise())
