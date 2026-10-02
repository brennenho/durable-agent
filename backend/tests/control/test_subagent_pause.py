import asyncio
from uuid import uuid4

import pytest
from durable_agent.contracts import ModelResult, SubmitFollowUpRequest, SubmitForkRequest
from durable_agent.control.continuations import create_continuation
from durable_agent.control.errors import ControlError
from durable_agent.control.forks import create_fork
from durable_agent.control.pause import set_pause
from durable_agent.control.views import detail
from durable_agent.db.models import Run
from sqlalchemy import select

from .test_delegation import spawn, waiting
from .test_leases import claimed
from .test_pause import pause
from .test_reports import report, report_body


def test_parent_pause_stops_child_claims_but_accepts_inflight_progress(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await waiting(db, config)
            await spawn(db, parent_id)
            first = await claimed(db)
            async with db.session() as session:
                await set_pause(session, parent_id, True)
                # No child claim can pass while the group pause holds its locks.
                assert await claimed(db) is None
                await session.commit()
            result = ModelResult(
                kind="model",
                native_context=[],
                output={
                    "status": "completed",
                    "text": "",
                    "tool_calls": [
                        {"call_id": "read", "name": "read_file", "arguments": {"path": "a"}}
                    ],
                },
            )
            await report(db, first, await report_body(db, first, result))
            assert await claimed(db) is None
            async with db.session() as session:
                saved = await detail(session, first.run_id)
                parent = await detail(session, parent_id)
                assert saved.pause_requested and saved.status == "queued"
                assert parent.pause_requested and parent.status == "waiting"
                assert saved.operations[-1].attempts == []
            await pause(db, parent_id, False)
            claims = [await claimed(db), await claimed(db)]
            resumed = next(item for item in claims if item.run_id == first.run_id)
            assert resumed.operation_id == saved.operations[-1].id
            assert resumed.operation.request.input_version_id == config.initial_workspace_version_id
            assert resumed.attempt_no == 1

    asyncio.run(exercise())


def test_pause_before_creation_is_inherited_and_terminal_children_stay_finished(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await waiting(db, config)
            await pause(db, parent_id)
            await spawn(db, parent_id)
            assert await claimed(db) is None
            await pause(db, parent_id, False)
            first = await claimed(db)
            await report(db, first, await report_body(db, first))
            await pause(db, parent_id)
            async with db.session() as session:
                child = await detail(session, first.run_id)
                assert child.status == "succeeded" and not child.pause_requested
                before = child.model_dump()
            assert await claimed(db) is None
            await pause(db, parent_id, False)
            assert (await claimed(db)).run_id != first.run_id
            async with db.session() as session:
                assert (await detail(session, first.run_id)).model_dump() == before

    asyncio.run(exercise())


def test_child_mutations_are_rejected_and_waiting_parent_blocks_followup(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await waiting(db, config)
            await spawn(db, parent_id)
            child = await claimed(db)
            await report(db, child, await report_body(db, child))
            async with db.session() as session:
                child_run = await session.get(Run, child.run_id)
                conversation_id = child_run.conversation_id
                count_before = len(list(await session.scalars(select(Run))))
            mutations = [
                lambda session: set_pause(session, child.run_id, True),
                lambda session: set_pause(session, child.run_id, False),
                lambda session: create_continuation(
                    session,
                    conversation_id,
                    SubmitFollowUpRequest(
                        submission_key=uuid4(), prompt="More", after_run_id=child.run_id
                    ),
                ),
                lambda session: create_fork(
                    session,
                    child.run_id,
                    SubmitForkRequest(
                        submission_key=uuid4(),
                        prompt="Branch",
                        after_operation_id=child.operation_id,
                    ),
                ),
            ]
            for mutation in mutations:
                async with db.session() as session:
                    with pytest.raises(ControlError) as caught:
                        await mutation(session)
                    assert caught.value.error.code == "child_run_read_only"
            async with db.session() as session:
                with pytest.raises(ControlError) as caught:
                    await create_continuation(
                        session,
                        parent_id,
                        SubmitFollowUpRequest(
                            submission_key=uuid4(), prompt="More", after_run_id=parent_id
                        ),
                    )
                assert caught.value.error.code == "conversation_busy"
                assert len(list(await session.scalars(select(Run)))) == count_before

    asyncio.run(exercise())
