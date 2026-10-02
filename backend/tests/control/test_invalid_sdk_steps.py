import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from durable_agent.control.agent_progress import start_agent
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.history import load_operations
from durable_agent.control.leases import claim
from durable_agent.sdk import Done, Next, ToolCall
from durable_agent.sdk.loading import AgentApp
from durable_agent.sdk.registration import agent

from .test_agent_progress import RUNTIME, added


@agent(name="broken", version="1")
class Broken:
    def start(self, ctx):
        return Next(state=None, step=ToolCall(name="run_command", arguments=ctx.input))

    def advance(self, ctx, result):
        return Done(output=None)


@pytest.mark.parametrize("arguments", [{}, {"command": "true", "timeout_seconds": 0}])
def test_invalid_sdk_step_fails_without_queuing_an_operation(database, config, arguments):
    async def exercise():
        runtime = AgentRuntime(AgentApp(agents=[Broken]), "app:app", "build")
        config.tool_schemas.append({"name": "run_command"})
        async with database() as db, db.session() as session:
            run = await added(session, config, arguments)
            run.config = runtime.freeze(config, "broken").model_dump(mode="json")
            await start_agent(session, run, runtime, datetime.now(UTC))
            await session.commit()
            assert run.status == "failed"
            assert run.terminal_error["code"] == "invalid_agent_step"
            assert await load_operations(session, run.id) == []

    asyncio.run(exercise())


def test_malformed_persisted_command_cannot_poison_the_claim_queue(database, config):
    async def exercise():
        async with database() as db, db.session() as session:
            broken = await added(session, config, "loop")
            await start_agent(session, broken, RUNTIME, datetime.now(UTC))
            await session.flush()
            operation = (await load_operations(session, broken.id))[0]
            operation.request = {
                **operation.request,
                "call": {"name": "run_command", "call_id": "bad", "arguments": {}},
            }
            healthy = await added(session, config, "loop")
            await start_agent(session, healthy, RUNTIME, datetime.now(UTC))
            await session.commit()
            assert await claim(session, uuid4(), agents=RUNTIME) is None
            await session.commit()
            assert broken.status == "failed"
            assert broken.terminal_error["code"] == "invalid_operation"
            assignment = await claim(session, uuid4(), agents=RUNTIME)
            assert assignment.run_id == healthy.id

    asyncio.run(exercise())
