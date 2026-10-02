"""Execute custom tools through the existing stopped-sandbox checkpoint path."""

import asyncio
import json
import math
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from durable_agent.contracts import ToolExecuteRequest, ToolOutput, ToolSuccess
from durable_agent.tools.commands import execute_command_tool
from durable_agent.tools.files import InvalidToolCall
from durable_agent.tools.invoke import INVALID_TOOL_EXIT
from durable_agent.tools.runner import DockerRunner, SandboxFailure


def decode_output(output: ToolSuccess) -> ToolOutput:
    value = output.value
    if value["exit_code"] == INVALID_TOOL_EXIT:
        raise InvalidToolCall(f"Custom tool failed: {value['stderr']}")
    if value["exit_code"] != 0:
        raise SandboxFailure(f"Custom tool process exited with code {value['exit_code']}")
    if value["truncated"]:
        raise InvalidToolCall("Custom tool output exceeds log_max_bytes")
    try:
        result = TypeAdapter(ToolOutput).validate_json(value["stdout"])
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ValidationError, ValueError) as exc:
        raise InvalidToolCall("Custom tool returned invalid JSON output") from exc
    return result


async def execute_custom_tool(
    archive: bytes,
    request: ToolExecuteRequest,
    runner: DockerRunner,
    workspace_root: Path,
    deadline: float,
    *,
    on_started: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[ToolOutput, bytes | None]:
    command = shlex.join(
        ["python", "-I", "-m", "durable_agent.tools.invoke", request.model_dump_json()]
    )
    seconds = min(900, max(1, math.ceil(deadline - asyncio.get_running_loop().time())))
    return await execute_command_tool(
        archive,
        {"command": command, "timeout_seconds": seconds},
        request.sandbox,
        runner,
        workspace_root,
        deadline,
        on_started=on_started,
        decode_output=decode_output,
    )
