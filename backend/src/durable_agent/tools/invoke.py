"""Run one installed developer tool inside the private sandbox process."""

import asyncio
import importlib
import inspect
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

from pydantic import BaseModel

from durable_agent.contracts import ToolError, ToolExecuteRequest, ToolOutput, ToolSuccess
from durable_agent.sdk.registration import BUILTIN_TOOLS
from durable_agent.sdk.tool_context import ToolContext
from durable_agent.sdk.workspace import Workspace

INVALID_TOOL_EXIT = 65


async def invoke_tool(request: ToolExecuteRequest, workspace: Path) -> ToolOutput:
    definition = request.definition
    if (
        definition is None
        or definition.name != request.name
        or request.name in BUILTIN_TOOLS
        or request.run_id is None
        or request.attempt_id is None
    ):
        raise ValueError("Custom tool requires a matching definition and call identity")
    with redirect_stdout(sys.stderr):
        module, separator, qualified_name = definition.entrypoint.partition(":")
        if not separator or not module or not qualified_name:
            raise ValueError("Tool entrypoint must be module:qualified_name")
        function = importlib.import_module(module)
        for name in qualified_name.split("."):
            function = getattr(function, name)
        if not inspect.iscoroutinefunction(function):
            raise ValueError("Custom tool must be an async function")
        if getattr(function, "__durable_tool__", None) != definition:
            raise ValueError("Installed tool definition differs from the saved definition")
        arguments_model = getattr(function, "__durable_arguments__", None)
        if not isinstance(arguments_model, type) or not issubclass(arguments_model, BaseModel):
            raise ValueError("Custom tool is missing its arguments model")
        arguments = arguments_model.model_validate_json(
            json.dumps(request.arguments, allow_nan=False), strict=True
        )
        values = {name: getattr(arguments, name) for name in arguments_model.model_fields}
        if definition.takes_context:
            values["ctx"] = ToolContext(
                run_id=request.run_id,
                operation_id=request.operation_id,
                attempt_id=request.attempt_id,
                workspace=Workspace(workspace),
                deadline=time.monotonic() + request.deadline_at.timestamp() - time.time(),
            )
        result = await function(**values)
    output = result if isinstance(result, ToolError) else ToolSuccess(status="ok", value=result)
    # JSON transport cannot represent NaN, infinity, or arbitrary Python objects.
    json.dumps(output.model_dump(mode="json"), allow_nan=False)
    return output


def main() -> None:
    # This is the dedicated sandbox process: native/subprocess logs must never use
    # the result channel, including logs emitted during interpreter shutdown.
    with os.fdopen(os.dup(1), "w", encoding="utf-8") as result_channel:
        os.dup2(2, 1)
        try:
            request = ToolExecuteRequest.model_validate_json(sys.argv[1])
            output = asyncio.run(invoke_tool(request, Path("/workspace")))
            print(output.model_dump_json(), file=result_channel)
        except Exception as exc:
            print(f"Custom tool failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            raise SystemExit(INVALID_TOOL_EXIT) from exc


if __name__ == "__main__":
    main()
