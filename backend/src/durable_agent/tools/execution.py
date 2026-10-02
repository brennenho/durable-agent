"""Run one registered tool call without holding a database transaction during work."""

import asyncio
from pathlib import Path

from sqlalchemy import func, select

from durable_agent.contracts import ToolExecuteRequest, ToolExecuteResponse
from durable_agent.crash_test import CrashTestHooks
from durable_agent.db.session import Database
from durable_agent.tools.commands import execute_command_tool
from durable_agent.tools.files import execute_file_tool
from durable_agent.tools.publication import publish_tool, register_tool
from durable_agent.tools.runner import DockerRunner, SandboxConfiguration
from durable_agent.tools.workspaces import load_workspace


async def execute_operation(
    db: Database,
    request: ToolExecuteRequest,
    slots: asyncio.Semaphore,
    *,
    runner: DockerRunner | None = None,
    workspace_root: Path | None = None,
    hooks: CrashTestHooks | None = None,
) -> ToolExecuteResponse:
    context = {"operation_id": request.operation_id, "lease_generation": request.lease_generation}

    async def sandbox_started(sandbox_id: str):
        await hooks.hit("sandbox.started", **context, sandbox_id=sandbox_id)

    async with db.session() as session:
        archive = await load_workspace(session, request.input_version_id)
        cached = await register_tool(session, request)
        if cached is None:
            started = asyncio.get_running_loop().time()
            now = await session.scalar(select(func.clock_timestamp()))
            deadline = started + (request.deadline_at - now).total_seconds()
        await session.commit()
    if cached is not None:
        if hooks:
            await hooks.hit(
                "tools.result_reused", **context, workspace_version_id=cached.workspace_version_id
            )
        return cached
    async with asyncio.timeout_at(deadline):
        if request.name == "run_command":
            if runner is None or workspace_root is None:
                raise SandboxConfiguration("Sandbox is not configured")
            async with slots:
                output, updated = await execute_command_tool(
                    archive,
                    request.arguments,
                    request.sandbox,
                    runner,
                    workspace_root,
                    deadline,
                    on_started=sandbox_started if hooks and hooks.client else None,
                )
        else:
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
        if hooks:
            await hooks.hit("tools.before_publish", **context)
        async with db.session() as session:
            response = await publish_tool(session, request, output, updated)
            await session.commit()
        if hooks:
            await hooks.hit(
                "tools.after_commit", **context, workspace_version_id=response.workspace_version_id
            )
        return response
