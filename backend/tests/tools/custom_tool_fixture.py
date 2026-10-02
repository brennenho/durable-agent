"""Installed as a developer module in custom-tool sandbox tests."""

import asyncio
import os
import signal
import sys

from durable_agent.contracts import ToolError
from durable_agent.sdk.registration import tool
from durable_agent.sdk.tool_context import ToolContext


@tool(name="save_note", version="1")
async def save_note(ctx: ToolContext, content: str = "default", mode: str = "ok"):
    print("developer log")
    if mode == "append":
        content = await ctx.workspace.read_text("note.txt") + content
    await ctx.workspace.write_text("note.txt", content)
    if mode == "native":
        await native_logs()
    if mode == "crash":
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == "sleep":
        await asyncio.sleep(120)
    if mode == "exception":
        raise RuntimeError("developer failure")
    if mode == "invalid":
        return ["not an object"]
    if mode == "nan":
        return {"number": float("nan")}
    if mode == "error":
        return ToolError(status="error", code="needs_review", message="Draft saved for review")
    return {
        "content": await ctx.workspace.read_text("note.txt"),
        "run_id": str(ctx.run_id),
        "operation_id": str(ctx.operation_id),
        "attempt_id": str(ctx.attempt_id),
        "remaining": ctx.remaining_seconds > 0,
    }


@tool(name="echo", version="1")
async def echo(content: str = "default", mode: str = "ok"):
    if mode == "native":
        await native_logs()
    return {"content": content}


async def native_logs():
    os.write(1, b"native log\n")
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", "print('child log')")
    if await child.wait() != 0:
        raise RuntimeError("Child process failed")
