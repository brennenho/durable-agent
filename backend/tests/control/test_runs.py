import asyncio
from uuid import uuid4

import pytest
from durable_agent.contracts import SubmitRunRequest
from durable_agent.control.errors import ControlError
from durable_agent.control.runs import create_run, find_submission
from durable_agent.db.models import Conversation, Operation, Run
from sqlalchemy import func, select

from .test_leases import claimed, queued
from .test_reports import report, report_body


def test_submission_persists_first_operation_and_freezes_config(database, config):
    async def exercise():
        async with database() as db:
            body = SubmitRunRequest(submission_key=uuid4(), prompt="Edit a file")
            async with db.session() as session:
                run = await create_run(session, body, config)
                run_id = run.id
                assert run.conversation_id == run.id
                assert run.turn_index == 0
                await session.commit()
            config.model = "changed-model"
            config.tool_schemas = [{"name": "invalid"}]
            async with db.session() as session:
                duplicate = await create_run(session, body, config)
                assert duplicate.id == run_id
                assert duplicate.config["model"] == "test"
                operation = await session.scalar(select(Operation))
                assert operation.run_id == run_id
                assert operation.kind == "model"
                assert operation.request == {"prompt": body.prompt, "history": []}
                assert operation.position == 0
                assert duplicate.status == "queued"
                changed = body.model_copy(update={"prompt": "Different"})
                with pytest.raises(ControlError) as caught:
                    await find_submission(session, changed)
                assert caught.value.status == 409

    asyncio.run(exercise())


@pytest.mark.parametrize("lookup", [True, False])
def test_initial_submission_rejects_a_follow_up_key(database, config, lookup):
    async def exercise():
        async with database() as db:
            conversation_id = await queued(db, config)
            assignment = await claimed(db)
            await report(db, assignment, await report_body(db, assignment))
            body = SubmitRunRequest(submission_key=uuid4(), prompt="Continue")
            async with db.session() as session:
                follow_up = Run(
                    id=uuid4(),
                    conversation_id=conversation_id,
                    turn_index=1,
                    submission_key=body.submission_key,
                    prompt=body.prompt,
                    config=config.model_dump(mode="json"),
                    status="queued",
                    next_attempt_at=func.clock_timestamp(),
                )
                session.add(follow_up)
                await session.flush()
                session.add(
                    Operation(
                        run_id=follow_up.id,
                        position=0,
                        kind="model",
                        request={"prompt": body.prompt, "history": []},
                    )
                )
                await session.commit()
            async with db.session() as session:
                with pytest.raises(ControlError) as caught:
                    if lookup:
                        await find_submission(session, body)
                    else:
                        await create_run(session, body, config)
                assert caught.value.status == 409
                assert caught.value.error.code == "submission_conflict"
                assert await session.scalar(select(func.count()).select_from(Conversation)) == 1
                assert await session.scalar(select(func.count()).select_from(Run)) == 2

    asyncio.run(exercise())


def test_interruption_before_commit_discards_run_and_first_operation(database, config):
    async def exercise():
        async with database() as db:
            async with db.session() as session:
                await create_run(
                    session, SubmitRunRequest(submission_key=uuid4(), prompt="Hi"), config
                )
            async with db.session() as session:
                for table in (Conversation, Run, Operation):
                    assert await session.scalar(select(func.count()).select_from(table)) == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("same_prompt", [True, False])
def test_concurrent_submissions_create_one_run_and_operation(database, config, same_prompt):
    async def exercise():
        async with database() as db:
            key = uuid4()

            async def submit(prompt):
                async with db.session() as session:
                    try:
                        run = await create_run(
                            session, SubmitRunRequest(submission_key=key, prompt=prompt), config
                        )
                        await session.commit()
                        return run.id
                    except ControlError as exc:
                        return exc.status

            results = await asyncio.gather(submit("Hi"), submit("Hi" if same_prompt else "Other"))
            if same_prompt:
                assert results[0] == results[1]
            else:
                assert results.count(409) == 1
            async with db.session() as session:
                for table in (Conversation, Run, Operation):
                    assert await session.scalar(select(func.count()).select_from(table)) == 1

    asyncio.run(exercise())
