"""Restore each command's fixed input and capture only a stopped sandbox's files."""

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from durable_agent.contracts import (
    RunCommandArguments,
    SandboxConfig,
    ToolError,
    ToolOutput,
    ToolSuccess,
)
from durable_agent.tools.archives import (
    InvalidWorkspace,
    capture_workspace,
    restore_workspace,
    workspace_path,
)
from durable_agent.tools.runner import DockerRunner, SandboxConfiguration


class InvalidCommand(ValueError):
    pass


def prepare_workspace_root(path: Path) -> None:
    """Discard this service's abandoned files after all its containers have stopped."""
    if path.is_symlink():
        raise SandboxConfiguration("Managed workspace root cannot be a symlink")
    if path.exists():
        path.chmod(0o700)
        for root, directories, _ in os.walk(path):
            for name in directories:
                directory = Path(root) / name
                if not directory.is_symlink():
                    directory.chmod(0o700)
        shutil.rmtree(path)
    path.mkdir(parents=True, mode=0o700)


async def filesystem(function, *args, **kwargs):
    """Finish local filesystem work before cancellation can clean up its directory."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def execute_command_tool(
    archive: bytes,
    arguments: dict,
    config: SandboxConfig,
    runner: DockerRunner,
    workspace_root: Path,
    deadline: float,
    *,
    on_started: Callable[[str], Awaitable[None]] | None = None,
    decode_output: Callable[[ToolSuccess], ToolOutput] | None = None,
) -> tuple[ToolOutput, bytes | None]:
    try:
        args = RunCommandArguments.model_validate(arguments, strict=True)
    except ValidationError as exc:
        raise InvalidCommand(f"Invalid run_command arguments: {exc}") from exc
    if "\0" in args.command:
        raise InvalidCommand("Command cannot contain NUL")
    try:
        cwd = "/workspace" if args.cwd == "." else f"/workspace/{workspace_path(args.cwd)}"
    except InvalidWorkspace as exc:
        return ToolError(status="error", code="invalid_path", message=str(exc)), None

    resources = ExitStack()

    def restore():
        directory = resources.enter_context(TemporaryDirectory(dir=workspace_root))
        workspace = Path(directory) / "workspace"
        restore_workspace(archive, workspace, config.workspace_max_bytes)
        if os.getuid() == 0:
            # The trusted service runs as root in Compose; sandbox code never does.
            for root, directories, files in os.walk(workspace):
                for name in directories + files:
                    os.chown(Path(root) / name, runner.uid, runner.gid)
            os.chown(workspace, runner.uid, runner.gid)
        return workspace

    try:
        async with asyncio.timeout_at(deadline):
            workspace = await filesystem(restore)
            output = await runner.run(
                workspace,
                args.command,
                cwd,
                config,
                deadline,
                timeout_seconds=args.timeout_seconds,
                on_started=on_started,
            )
            if isinstance(output, ToolError):
                return output, None
            if decode_output is not None:
                output = decode_output(output)
            updated = await filesystem(
                capture_workspace,
                workspace,
                config.workspace_max_bytes,
                allow_permission_changes=True,
            )
            return output, updated
    finally:
        await filesystem(resources.close)
