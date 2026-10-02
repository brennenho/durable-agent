import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from durable_agent.contracts import ModelResult
from durable_agent.control.agent_progress import start_agent
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.coordinator import REPORT_LIMIT
from durable_agent.db.models import Conversation, Run

from .test_coordinator import coordinate
from .test_leases import claimed
from .test_reports import report, report_body


def test_sdk_builtin_delegate_keeps_prompt_reports_and_worker_report_replay(database, config):
    async def exercise():
        async with database() as db:
            parent_id = uuid4()
            runtime = AgentRuntime()
            config.tool_schemas.append({"name": "delegate"})
            async with db.session() as session:
                session.add(Conversation(id=parent_id))
                await session.flush()
                parent = Run(
                    id=parent_id,
                    conversation_id=parent_id,
                    submission_key=uuid4(),
                    prompt="Review the workspace",
                    agent_input="Review the workspace",
                    config=runtime.freeze(config).model_dump(mode="json"),
                    status="queued",
                    next_attempt_at=datetime.now(UTC),
                )
                session.add(parent)
                await session.flush()
                await start_agent(session, parent, runtime, datetime.now(UTC))
                await session.commit()
            parent_call = await claimed(db)
            tasks = {"tasks": [{"name": "Review", "prompt": "Read the saved document"}]}
            result = ModelResult(
                kind="model",
                output={
                    "status": "completed",
                    "text": "",
                    "tool_calls": [
                        {
                            "call_id": "delegate-1",
                            "name": "delegate",
                            "arguments": tasks,
                        }
                    ],
                },
                native_context=[
                    {
                        "type": "function_call",
                        "call_id": "delegate-1",
                        "name": "delegate",
                        "arguments": json.dumps(tasks),
                    }
                ],
            )
            await report(db, parent_call, await report_body(db, parent_call, result))
            assert await claimed(db) is None
            assert (await coordinate(db))[0][0] == "control.after_children_commit"
            child_call = await claimed(db)
            assert child_call.run_id != parent_id
            assert child_call.config.agent.name == "builtin"
            assert child_call.operation.request.explicit_input == [
                {"role": "user", "content": "Read the saved document"},
            ]
            child_result = ModelResult(
                kind="model",
                native_context=[],
                output={"status": "completed", "text": "A" * (REPORT_LIMIT + 1), "tool_calls": []},
            )
            body = await report_body(db, child_call, child_result)
            assert await report(db, child_call, body) == "recorded"
            assert await report(db, child_call, body) == "already_recorded"
            assert (await coordinate(db))[0][0] == "control.after_join_commit"
            parent_call = await claimed(db)
            assert parent_call.run_id == parent_id
            item = parent_call.operation.request.explicit_input[-1]
            assert item["type"] == "function_call_output" and item["call_id"] == "delegate-1"
            joined = json.loads(item["output"])["value"]["children"][0]
            assert joined["name"] == "Review" and joined["report"] == "A" * REPORT_LIMIT
            assert joined["truncated"] and joined["output"] == "A" * (REPORT_LIMIT + 1)
            assert joined["error"] is None
            await report(db, parent_call, await report_body(db, parent_call))
            async with db.session() as session:
                parent = await session.get(Run, parent_id)
                assert parent.status == "succeeded" and parent.output == "Done"

    asyncio.run(exercise())
