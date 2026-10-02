import asyncio

from durable_agent.contracts import ModelRequest, ModelResult
from durable_agent.control.delegation import create_children
from durable_agent.control.views import detail
from durable_agent.db.models import Operation, Run
from sqlalchemy import select

from .test_leases import claimed, queued
from .test_reports import report, report_body


async def waiting(db, config):
    config.tool_schemas.append({"name": "delegate"})
    run_id = await queued(db, config)
    assignment = await claimed(db)
    result = ModelResult(
        kind="model",
        native_context=[],
        output={
            "status": "completed",
            "text": "",
            "tool_calls": [
                {
                    "call_id": "delegate-1",
                    "name": "delegate",
                    "arguments": {
                        "tasks": [
                            {"name": "Code", "prompt": "Inspect the implementation"},
                            {"name": "Tests", "prompt": "Inspect the tests"},
                        ]
                    },
                }
            ],
        },
    )
    await report(db, assignment, await report_body(db, assignment, result))
    return run_id


async def spawn(db, run_id, commit=True):
    async with db.session() as session:
        parent = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        operation = await session.scalar(
            select(Operation).where(Operation.run_id == run_id, Operation.completed_at.is_(None))
        )
        created = await create_children(session, parent, operation)
        if commit:
            await session.commit()
        return created


def test_children_are_atomic_repeatable_isolated_runs(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await waiting(db, config)
            assert await claimed(db) is None
            async with db.session() as session:
                before = await detail(session, parent_id)
                assert before.status == "waiting" and before.next_attempt_at is None
                assert before.operations[-1].attempts == []
                input_version = config.initial_workspace_version_id
            assert await spawn(db, parent_id, commit=False)
            async with db.session() as session:
                assert len(list(await session.scalars(select(Run)))) == 1
            assert sorted(await asyncio.gather(spawn(db, parent_id), spawn(db, parent_id))) == [
                False,
                True,
            ]
            async with db.session() as session:
                children = list(
                    await session.scalars(
                        select(Run)
                        .where(Run.parent_operation_id == before.operations[-1].id)
                        .order_by(Run.child_index)
                    )
                )
                assert len(children) == 2
                assert {child.conversation_id for child in children}.isdisjoint({parent_id})
                assert len({child.conversation_id for child in children}) == 2
                for index, child in enumerate(children):
                    assert child.child_index == index
                    assert child.config["initial_workspace_version_id"] == str(input_version)
                    assert child.config["tool_schemas"] == config.tool_schemas[:-1]
                    assert child.config["model"] == config.model
                    assert child.config["max_attempts"] == config.max_attempts
                    first = await session.scalar(
                        select(Operation).where(Operation.run_id == child.id)
                    )
                    assert ModelRequest.model_validate(first.request) == ModelRequest(
                        prompt=child.prompt, history=[], previous_turns=[]
                    )
            first, second = await claimed(db), await claimed(db)
            assert {first.run_id, second.run_id} == {child.id for child in children}
            assert first.attempt_no == second.attempt_no == 1
            assert await claimed(db) is None

    asyncio.run(exercise())
