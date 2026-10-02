import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.contracts import SubmitForkRequest, ToolResult
from durable_agent.control.agent_progress import advance_agent, start_agent
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.control.forks import create_fork
from durable_agent.control.history import load_operations
from durable_agent.db.models import Conversation, Run

from .test_forks import counts
from .test_history import model
from .test_sdk_continuations import agents as agents
from .test_sdk_continuations import source_run


async def fork(db, source, body, runtime=None, *, commit=True):
    async with db.session() as session:
        run = await create_fork(session, source.id, body, agents=runtime)
        if commit:
            await session.commit()
        return run


@pytest.mark.parametrize("selected_state", [None, {"saved": 1}])
def test_sdk_fork_uses_selected_state_history_and_files_not_latest(
    database, config, agents, selected_state
):
    async def exercise():
        async with database() as db:
            source, version = await source_run(db, config, agents)
            async with db.session() as session:
                saved_source = await session.get(Run, source.id)
                selected, later = await load_operations(session, source.id)
                selected.agent_state = selected_state
                later.result = {**selected.result, "workspace_version_id": str(uuid4())}
                later.completed_at = datetime.now(UTC)
                later.agent_state = saved_source.agent_state = {"saved": 2}
                await session.commit()
            body = SubmitForkRequest(
                submission_key=uuid4(), input=True, after_operation_id=selected.id
            )
            before = await counts(db)
            await fork(db, source, body, agents, commit=False)
            assert await counts(db) == before
            branch = await fork(db, source, body, agents)
            assert branch.id == branch.conversation_id != source.conversation_id
            assert branch.status == "succeeded"
            assert branch.output["state"] == branch.agent_state == selected_state
            assert branch.output["input"] is True
            assert branch.output["workspace_version_id"] == str(version)
            assert [h["operation_id"] for h in branch.output["history"]] == [
                source.config["agent_history"][0]["operation_id"],
                str(selected.id),
            ]
            assert branch.config == {
                **source.config,
                "initial_workspace_version_id": str(version),
                "agent_history": branch.output["history"],
            }
            assert (await fork(db, source, body)).id == branch.id
            assert await counts(db) == [before[0] + 1, before[1] + 1, before[2]]
            async with db.session() as session:
                assert (await session.get(Run, source.id)).agent_state == {"saved": 2}
                origin = await session.get(Conversation, branch.conversation_id)
                assert origin.forked_from_operation_id == selected.id
                assert await load_operations(session, branch.id) == []
            for change in ({"input": 1}, {"agent": "other"}):
                with pytest.raises(ControlError) as caught:
                    await fork(db, source, body.model_copy(update=change), agents)
                assert caught.value.status == 409
                assert caught.value.error.code == "submission_conflict"

    asyncio.run(exercise())


def test_sdk_fork_rejects_unaccepted_checkpoints_and_deduplicates_concurrent_submissions(
    database, config, agents
):
    async def exercise():
        async with database() as db:
            source, _ = await source_run(db, config, agents)
            async with db.session() as session:
                selected, pending = await load_operations(session, source.id)
            body = SubmitForkRequest(
                submission_key=uuid4(), input="wait", after_operation_id=pending.id
            )
            before = await counts(db)
            with pytest.raises(ControlError) as caught:
                await fork(db, source, body, agents)
            assert caught.value.error.code == "checkpoint_not_available"
            body.after_operation_id = selected.id
            first, replay = await asyncio.gather(
                fork(db, source, body, agents), fork(db, source, body, agents)
            )
            assert first.id == replay.id and first.status == "queued"
            assert await counts(db) == [value + 1 for value in before]

    asyncio.run(exercise())


def test_builtin_sdk_forks_only_after_closed_rounds(database, config):
    async def exercise():
        runtime = AgentRuntime()
        async with database() as db:
            async with db.session() as session:
                run_id, now = uuid4(), datetime.now(UTC)
                session.add(Conversation(id=run_id))
                await session.flush()
                source = Run(
                    id=run_id,
                    conversation_id=run_id,
                    submission_key=uuid4(),
                    prompt="Edit",
                    agent_input="Edit",
                    agent_state=None,
                    config=runtime.freeze(config).model_dump(mode="json"),
                    status="queued",
                    next_attempt_at=now,
                )
                session.add(source)
                await session.flush()
                await start_agent(session, source, runtime, now)
                plan = model(
                    *[
                        {
                            "call_id": key,
                            "name": "write_file",
                            "arguments": {"path": key, "content": key},
                        }
                        for key in ("a", "b")
                    ]
                )
                plan.native_context = [
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    }
                    for call in plan.output.tool_calls
                ]
                results = [
                    plan,
                    *[
                        ToolResult(
                            kind="tool",
                            call_id=key,
                            output={"status": "ok", "value": {}},
                            workspace_version_id=uuid4(),
                        )
                        for key in ("a", "b")
                    ],
                ]
                for result in results:
                    await session.flush()
                    operation = (await load_operations(session, run_id))[-1]
                    await advance_agent(session, source, operation, result, now, runtime)
                await session.commit()
                operations = await load_operations(session, run_id)
            for index in (0, 1, 3):
                with pytest.raises(ControlError) as caught:
                    await fork(
                        db,
                        source,
                        SubmitForkRequest(
                            submission_key=uuid4(),
                            prompt="Branch",
                            after_operation_id=operations[index].id,
                        ),
                        runtime,
                    )
                assert caught.value.error.code == "checkpoint_not_available"
            branch = await fork(
                db,
                source,
                SubmitForkRequest(
                    submission_key=uuid4(), prompt="Branch", after_operation_id=operations[2].id
                ),
                runtime,
            )
            assert branch.status == "queued" and branch.agent_input == branch.prompt == "Branch"
            assert branch.config["initial_workspace_version_id"] == str(
                results[-1].workspace_version_id
            )
            async with db.session() as session:
                first = (await load_operations(session, branch.id))[0]
                assert first.request["explicit_input"][-1] == {"role": "user", "content": "Branch"}

    asyncio.run(exercise())
