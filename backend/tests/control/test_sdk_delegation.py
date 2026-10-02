import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.contracts import ToolResult
from durable_agent.control.agent_progress import advance_agent, start_agent
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.coordinator import advance_delegations
from durable_agent.control.history import load_operations
from durable_agent.db.models import Conversation, Run
from durable_agent.sdk import ChildBatch, Done, Fail, Next, StepResult, ToolCall
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.registration import agent
from sqlalchemy import select


@agent(name="parent", version="1")
class Parent:
    def start(self, ctx):
        return Next(state={"parent_only": True}, step=ChildBatch(tasks=ctx.input))

    def advance(self, ctx, result):
        return Done(output=result.value)


@agent(name="child", version="2")
class Child:
    def start(self, ctx):
        assert ctx.state is None and not ctx.history
        if ctx.input == "fail":
            return Fail(code="declined", message="Child declined the task")
        if ctx.input == "recurse":
            return Next(
                state=None,
                step=ChildBatch(
                    tasks=[
                        {"name": "nested", "agent": "child", "input": None},
                    ]
                ),
            )
        if ctx.input == "write":
            return Next(
                state=None,
                step=ToolCall(
                    name="write_file",
                    arguments={"path": "child.txt", "content": "private"},
                ),
            )
        return Done(output=ctx.input)

    def advance(self, ctx, result):
        return Done(output=result.value)


RUNTIME = AgentRuntime(AgentApp(agents=[Parent, Child]), "example:app", "build-1")


async def parent_run(db, config, inputs, *, names=None):
    async with db.session() as session:
        run_id = uuid4()
        session.add(Conversation(id=run_id))
        await session.flush()
        config = RUNTIME.freeze(config, "parent")
        config.agent_history = [
            StepResult(operation_id=uuid4(), kind="tool", value="parent history")
        ]
        run = Run(
            id=run_id,
            conversation_id=run_id,
            submission_key=uuid4(),
            prompt="Parent task",
            config=config.model_dump(mode="json"),
            status="queued",
            next_attempt_at=datetime.now(UTC),
            agent_input=[
                {
                    "name": f"task{index}",
                    "agent": (names or ["child"] * len(inputs))[index],
                    "input": value,
                }
                for index, value in enumerate(inputs)
            ],
        )
        session.add(run)
        await session.flush()
        await start_agent(session, run, RUNTIME, datetime.now(UTC))
        await session.commit()
        return run_id


async def scan(db, *, commit=True, agents=RUNTIME):
    async with db.session() as session:
        events = await advance_delegations(session, agents=agents)
        if commit:
            await session.commit()
        return events


async def children(session, parent_id):
    operation = (await load_operations(session, parent_id))[0]
    return list(
        await session.scalars(
            select(Run).where(Run.parent_operation_id == operation.id).order_by(Run.child_index)
        )
    )


def test_terminal_children_join_false_null_and_failure_without_model_operations(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await parent_run(db, config, [False, None, "fail"])
            assert (await scan(db))[0][0] == "control.after_children_commit"
            async with db.session() as session:
                saved = await children(session, parent_id)
                ids = [child.id for child in saved]
                assert [child.status for child in saved] == ["succeeded", "succeeded", "failed"]
                for child in saved:
                    assert await load_operations(session, child.id) == []
            assert (await scan(db))[0][0] == "control.after_join_commit"
            assert await scan(db) == []
            async with db.session() as session:
                parent = await session.get(Run, parent_id)
                assert parent.status == "succeeded"
                reports = parent.output["children"]
                assert [row["run_id"] for row in reports] == [str(key) for key in ids]
                assert reports[0]["output"] is False and reports[1]["output"] is None
                assert all(row["report"] is None for row in reports)
                assert reports[2]["error"]["code"] == "declined"
                assert (await load_operations(session, parent_id))[0].result[
                    "workspace_version_id"
                ] == str(config.initial_workspace_version_id)

    asyncio.run(exercise())


def test_tool_first_children_are_isolated_and_recovery_does_not_duplicate_progress(
    database, config
):
    async def exercise():
        async with database() as db:
            parent_id = await parent_run(db, config, ["write", "write"])
            assert await scan(db, commit=False)
            async with db.session() as session:
                assert await children(session, parent_id) == []
            scans = await asyncio.gather(scan(db), scan(db))
            assert sorted(len(events) for events in scans) == [0, 1]
            assert await scan(db) == []
            async with db.session() as session:
                saved = await children(session, parent_id)
                ids = [child.id for child in saved]
                assert len(set(ids)) == 2 and parent_id not in ids
                for child in saved:
                    await session.refresh(child, with_for_update=True)
                    assert child.conversation_id == child.id
                    assert child.config["agent"]["name"] == "child"
                    assert child.config["agent"]["version"] == "2"
                    assert child.config["agent"]["build_id"] == RUNTIME.build_id
                    operation = (await load_operations(session, child.id))[0]
                    assert operation.kind == "tool"
                    assert operation.request["input_version_id"] == str(
                        config.initial_workspace_version_id
                    )
                    await advance_agent(
                        session,
                        child,
                        operation,
                        ToolResult(
                            kind="tool",
                            call_id=operation.request["call"]["call_id"],
                            output={"status": "ok", "value": {"saved": True}},
                            workspace_version_id=uuid4(),
                        ),
                        datetime.now(UTC),
                        RUNTIME,
                    )
                await session.commit()
            assert await scan(db, commit=False)
            async with db.session() as session:
                assert (await session.get(Run, parent_id)).status == "waiting"
            scans = await asyncio.gather(scan(db), scan(db))
            assert sorted(len(events) for events in scans) == [0, 1]
            async with db.session() as session:
                parent = await session.get(Run, parent_id)
                assert parent.status == "succeeded" and parent.lease_owner is None
                assert [child.id for child in await children(session, parent_id)] == ids
                (operation,) = await load_operations(session, parent_id)
                assert operation.result["workspace_version_id"] == str(
                    config.initial_workspace_version_id
                )
                assert all(item["output"] == {"saved": True} for item in parent.output["children"])

    asyncio.run(exercise())


@pytest.mark.parametrize("changed_build", [False, True])
def test_invalid_child_deployment_fails_parent_before_creating_any_child(
    database, config, changed_build
):
    async def exercise():
        async with database() as db:
            parent_id = await parent_run(
                db,
                config,
                [False, None],
                names=[
                    "child",
                    "child" if changed_build else "missing",
                ],
            )
            runtime = (
                AgentRuntime(RUNTIME.app, RUNTIME.app_ref, "changed") if changed_build else RUNTIME
            )
            assert await scan(db, agents=runtime) == []
            async with db.session() as session:
                parent = await session.get(Run, parent_id)
                assert parent.status == "failed"
                assert parent.terminal_error["code"] == (
                    "agent_incompatible" if changed_build else "unknown_agent"
                )
                assert await children(session, parent_id) == []
            assert await scan(db) == []

    asyncio.run(exercise())


def test_child_cannot_delegate_again(database, config):
    async def exercise():
        async with database() as db:
            parent_id = await parent_run(db, config, ["recurse"])
            await scan(db)
            await scan(db)
            async with db.session() as session:
                parent = await session.get(Run, parent_id)
                assert parent.status == "succeeded"
                assert parent.output["children"][0]["error"]["code"] == "invalid_agent_step"
                assert len(list(await session.scalars(select(Run)))) == 2

    asyncio.run(exercise())
