"""Run one registered tool call without holding a database transaction during work."""

import asyncio

from sqlalchemy import func, select

from durable_agent.contracts import ToolExecuteRequest, ToolExecuteResponse
from durable_agent.db.session import Database
from durable_agent.tools.files import execute_file_tool
from durable_agent.tools.publication import publish_tool, register_tool
from durable_agent.tools.workspaces import load_workspace


async def execute_operation(
    db: Database, request: ToolExecuteRequest, slots: asyncio.Semaphore
) -> ToolExecuteResponse:
    if request.name == "run_command":
        raise NotImplementedError("Sandbox command execution is not implemented")
    async with db.session() as session:
        archive = await load_workspace(session, request.input_version_id)
        cached = await register_tool(session, request)
        if cached is not None:
            await session.commit()
            return cached
        started = asyncio.get_running_loop().time()
        now = await session.scalar(select(func.clock_timestamp()))
        deadline = started + (request.deadline_at - now).total_seconds()
        await session.commit()
    async with asyncio.timeout_at(deadline):
        await slots.acquire()
        job = asyncio.create_task(
            asyncio.to_thread(
                execute_file_tool,
                archive,
                request.name,
                request.arguments,
                request.sandbox.workspace_max_bytes,
            )
        )
        # Threads cannot be cancelled: keep their capacity reserved until they finish.
        job.add_done_callback(lambda completed: slots.release())
        output, updated = await asyncio.shield(job)
        async with db.session() as session:
            response = await publish_tool(session, request, output, updated)
            await session.commit()
            return response
