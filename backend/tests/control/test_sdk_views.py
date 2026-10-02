import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.contracts import ToolResult
from durable_agent.control.agent_progress import advance_agent, start_agent
from durable_agent.control.conversations import conversation_detail, list_conversations, title
from durable_agent.control.history import load_operations
from durable_agent.control.views import detail, list_summaries

from .test_agent_progress import RUNTIME, added


@pytest.mark.parametrize("value,output", [("done", [1, 2]), ("fail", None)])
def test_immediate_sdk_run_has_identity_output_and_seed_workspace(database, config, value, output):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config, value)
            await start_agent(session, run, RUNTIME, datetime.now(UTC))
            await session.commit()
            view = await detail(session, run.id)
            assert view.conversation_id == run.conversation_id
            assert view.agent.name == "counter"
            assert view.output == output
            assert view.operations == []
            assert view.workspace_version_id == config.initial_workspace_version_id
            summaries = await list_summaries(session)
            assert summaries[0].agent == view.agent
            assert summaries[0].conversation_id == view.conversation_id

    asyncio.run(exercise())


def test_custom_tool_first_loop_has_checkpoints_and_structured_output(database, config):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config, "loop")
            await start_agent(session, run, RUNTIME, datetime.now(UTC))
            await session.flush()
            versions = [uuid4(), uuid4()]
            for index, version in enumerate(versions):
                operation = (await load_operations(session, run.id))[index]
                result = ToolResult(
                    kind="tool",
                    call_id=operation.request["call"]["call_id"],
                    output={"status": "ok", "value": {"text": "saved"}},
                    workspace_version_id=version,
                )
                await advance_agent(session, run, operation, result, datetime.now(UTC), RUNTIME)
                await session.flush()
            await session.commit()
            view = await detail(session, run.id)
            assert view.status == "succeeded"
            assert view.output == {"count": 2}
            assert view.final_answer is None
            assert view.workspace_version_id == versions[-1]
            assert [op.checkpoint.workspace_version_id for op in view.operations] == versions
            assert [op.tool_name for op in view.operations] == ["read_file", "read_file"]

    asyncio.run(exercise())


@pytest.mark.parametrize("value", [None, False, "", [], {"title": "Saved input"}])
def test_sdk_input_and_conversation_identity_are_preserved(database, config, value):
    async def exercise():
        async with database() as db, db.session() as session:
            run = await added(session, config, value)
            await start_agent(session, run, RUNTIME, datetime.now(UTC))
            await session.commit()
            view = await detail(session, run.id)
            assert view.input == value
            assert view.agent.name == "counter"
            conversation = await conversation_detail(session, run.conversation_id)
            assert conversation.title == "counter"
            assert conversation.runs[0].input == value
            listed = await list_conversations(session)
            assert listed[0].title == conversation.title

    asyncio.run(exercise())


def test_conversation_title_preserves_prompt_and_builtin_fallback():
    assert title("  Existing\n title ", "counter") == "Existing title"
    assert title("", "builtin") == "New chat"
    assert title("") == "New chat"
