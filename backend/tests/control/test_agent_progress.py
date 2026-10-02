import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.contracts import ToolResult
from durable_agent.control.agent_progress import advance_agent, start_agent
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.control.history import load_operations
from durable_agent.db.models import Conversation, Run
from durable_agent.sdk import Done, Fail, Next, ToolCall
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.registration import agent
from sqlalchemy import select


@agent(name="counter", version="1")
class Counter:
    def start(self, ctx):
        if ctx.input == "done":
            return Done(output=[1, 2])
        if ctx.input == "fail":
            return Fail(code="declined", message="Cannot proceed")
        return Next(state=0, step=ToolCall(name="read_file", arguments={"path": "a"}))

    def advance(self, ctx, result):
        assert len(ctx.history) == ctx.state
        if ctx.state == 1:
            return Done(output={"count": 2}, state=None)
        return Next(state=1, step=ToolCall(name="read_file", arguments={"path": "b"}))


RUNTIME = AgentRuntime(AgentApp(agents=[Counter]), "example:app", "build-1")


async def added(session, config, value):
    run_id = uuid4()
    session.add(Conversation(id=run_id))
    await session.flush()
    run = Run(
        id=run_id,
        conversation_id=run_id,
        submission_key=uuid4(),
        prompt="",
        config=RUNTIME.freeze(config, "counter").model_dump(mode="json"),
        agent_input=value,
        agent_state={"inherited": True},
        status="queued",
        next_attempt_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run


@pytest.mark.parametrize(
    "value, status, output", [("done", "succeeded", [1, 2]), ("fail", "failed", None)]
)
def test_immediate_terminal_decisions_need_no_operation(database, config, value, status, output):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config, value)
            await start_agent(session, run, RUNTIME, datetime.now(UTC))
            await session.commit()
            assert run.status == status
            assert run.output == output
            assert run.agent_state == {"inherited": True}
            assert run.finished_at is not None
            assert await load_operations(session, run.id) == []

    asyncio.run(exercise())


def test_state_result_and_next_operation_commit_together(database, config):
    async def exercise():
        async with database() as db:
            async with db.session() as session:
                run = await added(session, config, "loop")
                await start_agent(session, run, RUNTIME, datetime.now(UTC))
                await session.commit()
                run_id = run.id
            version = uuid4()
            for commit in (False, True):
                async with db.session() as session:
                    run = await session.scalar(
                        select(Run).where(Run.id == run_id).with_for_update()
                    )
                    operation = (await load_operations(session, run_id))[0]
                    result = ToolResult(
                        kind="tool",
                        call_id=operation.request["call"]["call_id"],
                        output={"status": "ok", "value": {"text": "saved"}},
                        workspace_version_id=version,
                    )
                    await advance_agent(session, run, operation, result, datetime.now(UTC), RUNTIME)
                    await session.flush()
                    if commit:
                        await session.commit()
                async with db.session() as session:
                    run = await session.get(Run, run_id)
                    operations = await load_operations(session, run_id)
                    assert run.agent_state == (1 if commit else 0)
                    assert len(operations) == (2 if commit else 1)
                    assert operations[0].result == (
                        result.model_dump(mode="json") if commit else None
                    )
                    assert operations[0].agent_state == (1 if commit else None)
                    if commit:
                        assert operations[1].request["input_version_id"] == str(version)
                        with pytest.raises(ControlError, match="already has an accepted result"):
                            await advance_agent(
                                session, run, operations[0], result, datetime.now(UTC), RUNTIME
                            )
                        await advance_agent(
                            session,
                            run,
                            operations[1],
                            result.model_copy(
                                update={"call_id": operations[1].request["call"]["call_id"]}
                            ),
                            datetime.now(UTC),
                            RUNTIME,
                        )
                        await session.commit()
                        assert run.status == "succeeded"
                        assert run.output == {"count": 2}
                        assert run.agent_state is None
                        assert operations[1].agent_state is None

    asyncio.run(exercise())


def test_incompatible_deployment_is_a_terminal_error(database, config):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config, "done")
            changed = AgentRuntime(RUNTIME.app, "example:app", "build-2")
            await start_agent(session, run, changed, datetime.now(UTC))
            await session.commit()
            assert run.status == "failed"
            assert run.terminal_error["code"] == "agent_incompatible"
            assert run.output is None
            assert await load_operations(session, run.id) == []

    asyncio.run(exercise())


def test_step_limit_accepts_result_but_does_not_schedule_more_work(database, config):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config.model_copy(update={"max_steps": 1}), "loop")
            await start_agent(session, run, RUNTIME, datetime.now(UTC))
            await session.flush()
            operation = (await load_operations(session, run.id))[0]
            result = ToolResult(
                kind="tool",
                call_id=operation.request["call"]["call_id"],
                output={"status": "ok", "value": {}},
                workspace_version_id=uuid4(),
            )
            await advance_agent(session, run, operation, result, datetime.now(UTC), RUNTIME)
            await session.commit()
            assert run.status == "failed"
            assert run.terminal_error["message"] == "Agent step limit reached"
            assert operation.completed_at is not None
            assert len(await load_operations(session, run.id)) == 1

    asyncio.run(exercise())
