import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.contracts import SubmitFollowUpRequest, ToolResult
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.continuations import create_continuation
from durable_agent.control.errors import ControlError
from durable_agent.control.history import load_operations
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.sdk import Done, ModelCall, Next, StepResult
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.registration import agent, tool
from sqlalchemy import func, select


@agent(name="remember", version="1")
class Remember:
    def start(self, ctx):
        if ctx.input is None:
            return Done(output=None, state=None)
        if ctx.input == "wait":
            return Next(state=ctx.state, step=ModelCall(prompt="Work"))
        return Done(output=ctx.model_dump(mode="json"))

    def advance(self, ctx, result):
        return Done(output=result.value)


@tool(name="tag", version="1")
async def tag(value: str):
    return value


@pytest.fixture
def agents():
    return AgentRuntime(AgentApp(agents=[Remember], tools=[tag]), "tests:app", "build-1")


async def source_run(db, config, agents):
    frozen = agents.freeze(config, name="remember")
    frozen.agent_history = [StepResult(operation_id=uuid4(), kind="model", value="Earlier turn")]
    run_id, version = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with db.session() as session:
        session.add(Conversation(id=run_id))
        await session.flush()
        run = Run(
            id=run_id,
            conversation_id=run_id,
            submission_key=uuid4(),
            prompt="",
            agent_input={"original": True},
            agent_state={"saved": 1},
            config=frozen.model_dump(mode="json"),
            status="failed",
            finished_at=now,
            terminal_error={"code": "stopped", "message": "Stopped", "retryable": False},
        )
        session.add(run)
        await session.flush()
        for position in range(2):
            result = ToolResult(
                kind="tool",
                call_id=str(position),
                output={"status": "ok", "value": {}},
                workspace_version_id=version,
            )
            session.add(
                Operation(
                    id=uuid4(),
                    run_id=run_id,
                    position=position,
                    kind="tool",
                    request={
                        "call": {
                            "call_id": str(position),
                            "name": "write_file",
                            "arguments": {"path": "file", "content": "saved"},
                        },
                        "input_version_id": str(config.initial_workspace_version_id),
                    },
                    result=result.model_dump(mode="json") if position == 0 else None,
                    completed_at=now if position == 0 else None,
                    agent_state={"saved": 1} if position == 0 else {"unaccepted": True},
                )
            )
        await session.commit()
        return run, version


def follow_up(run, value, **fields):
    return SubmitFollowUpRequest(submission_key=uuid4(), after_run_id=run.id, input=value, **fields)


async def submit(db, source, body, agents=None, *, commit=True):
    async with db.session() as session:
        run = await create_continuation(session, source.conversation_id, body, agents=agents)
        if commit:
            await session.commit()
        return run


def test_sdk_follow_up_inherits_only_accepted_progress_and_rolls_back_atomically(
    database, config, agents
):
    async def exercise():
        async with database() as db:
            source, version = await source_run(db, config, agents)
            body = follow_up(source, {"new": "input"})
            await submit(db, source, body, agents, commit=False)
            async with db.session() as session:
                assert await session.scalar(select(func.count()).select_from(Run)) == 1
            run = await submit(db, source, body, agents)
            assert run.status == "succeeded" and run.agent_input == body.input
            assert run.output["input"] == body.input
            assert run.output["state"] == run.agent_state == source.agent_state
            assert run.output["workspace_version_id"] == str(version)
            async with db.session() as session:
                operations = await load_operations(session, source.id)
                assert [h["operation_id"] for h in run.output["history"]] == [
                    source.config["agent_history"][0]["operation_id"],
                    str(operations[0].id),
                ]
                assert await load_operations(session, run.id) == []
            assert run.config == {
                **source.config,
                "initial_workspace_version_id": str(version),
                "agent_history": run.output["history"],
            }
            await submit(db, run, follow_up(run, "wait"), agents)
            assert (await submit(db, source, body)).id == run.id
            assert (
                await submit(db, source, body.model_copy(update={"agent": "remember"}))
            ).id == run.id

    asyncio.run(exercise())


def test_immediate_null_completion_can_continue_without_operations(database, config, agents):
    async def exercise():
        async with database() as db:
            source, version = await source_run(db, config, agents)
            empty = await submit(db, source, follow_up(source, None), agents)
            assert (
                empty.status == "succeeded" and empty.output is None and empty.agent_state is None
            )
            later = await submit(db, empty, follow_up(empty, "inspect"), agents)
            assert later.output["state"] is None
            assert later.output["workspace_version_id"] == str(version)
            assert later.output["history"] == empty.config["agent_history"]

    asyncio.run(exercise())


def test_builtin_sdk_accepts_prompt_and_replays_equivalent_json_input(database, config):
    async def exercise():
        async with database() as db:
            run_id = uuid4()
            async with db.session() as session:
                session.add(Conversation(id=run_id))
                await session.flush()
                source = Run(
                    id=run_id,
                    conversation_id=run_id,
                    submission_key=uuid4(),
                    prompt="",
                    agent_input=None,
                    agent_state=None,
                    config=AgentRuntime().freeze(config).model_dump(mode="json"),
                    status="failed",
                    finished_at=datetime.now(UTC),
                    terminal_error={
                        "code": "invalid_input",
                        "message": "Text required",
                        "retryable": False,
                    },
                )
                session.add(source)
                await session.commit()
            body = SubmitFollowUpRequest(
                submission_key=uuid4(), after_run_id=source.id, prompt="Continue"
            )
            run = await submit(db, source, body)
            assert run.status == "queued" and run.prompt == run.agent_input == "Continue"
            async with db.session() as session:
                saved = (await load_operations(session, run.id))[0]
                assert saved.request["explicit_input"] == [{"role": "user", "content": "Continue"}]
            replay = SubmitFollowUpRequest(
                submission_key=body.submission_key,
                after_run_id=source.id,
                input="Continue",
                agent="builtin",
            )
            assert (await submit(db, source, replay)).id == run.id

    asyncio.run(exercise())


@pytest.mark.parametrize("change", [{"input": 1}, {"agent": "other"}])
def test_sdk_submission_key_checks_input_and_agent(database, config, agents, change):
    async def exercise():
        async with database() as db:
            source, _ = await source_run(db, config, agents)
            body = follow_up(source, True)
            await submit(db, source, body, agents)
            with pytest.raises(ControlError) as caught:
                await submit(db, source, body.model_copy(update=change), agents)
            assert caught.value.status == 409
            assert caught.value.error.code == "submission_conflict"

    asyncio.run(exercise())


def test_sdk_follow_up_rejects_agent_changes_and_unavailable_code(database, config, agents):
    async def exercise():
        async with database() as db:
            source, _ = await source_run(db, config, agents)
            for body, runtime, code in [
                (follow_up(source, "next", agent="other"), agents, "agent_mismatch"),
                (
                    SubmitFollowUpRequest(
                        submission_key=uuid4(), after_run_id=source.id, prompt="legacy"
                    ),
                    agents,
                    "agent_mismatch",
                ),
            ]:
                with pytest.raises(ControlError) as caught:
                    await submit(db, source, body, runtime)
                assert caught.value.error.code == code
            changed = AgentRuntime(agents.app, agents.app_ref, "different-build")
            for runtime in (None, changed):
                failed = await submit(db, source, follow_up(source, "next"), runtime)
                assert failed.status == "failed"
                assert failed.terminal_error["code"] == "agent_incompatible"
                async with db.session() as session:
                    assert await load_operations(session, failed.id) == []
                source = failed

    asyncio.run(exercise())


def test_concurrent_sdk_follow_ups_share_one_active_run(database, config, agents):
    async def exercise():
        async with database() as db:
            source, _ = await source_run(db, config, agents)
            body = follow_up(source, "wait")
            first, replay = await asyncio.gather(
                submit(db, source, body, agents), submit(db, source, body, agents)
            )
            assert first.id == replay.id and first.status == "queued"
            with pytest.raises(ControlError) as caught:
                await submit(db, first, follow_up(first, "next"), agents)
            assert caught.value.error.code == "conversation_busy"
            async with db.session() as session:
                assert await session.scalar(select(func.count()).select_from(Run)) == 2
                assert len(await load_operations(session, first.id)) == 1

    asyncio.run(exercise())
