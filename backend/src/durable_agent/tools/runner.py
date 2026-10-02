"""Trusted Docker launcher. Callers own the private workspace and its checkpoint."""

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from durable_agent.contracts import SandboxConfig, ToolOutput
from durable_agent.tools import supervisor


class SandboxFailure(RuntimeError):
    """Execution has no trustworthy completion; retry from the saved input."""


class SandboxConfiguration(ValueError):
    pass


def validate_image(image: str) -> None:
    if not re.fullmatch(r"(?:[\w./:-]+@)?sha256:[0-9a-f]{64}", image):
        raise SandboxConfiguration("Sandbox image must be pinned by digest or image ID")


class DockerRunner:
    def __init__(self, *, docker: str = "docker", owner: str = "durable-agent-tools"):
        self.docker = docker
        self.owner = owner
        self.uid = os.getuid() or 1000
        self.gid = os.getgid() if os.getuid() else 1000

    async def _cli(self, *args: str, limit: int = 1024 * 1024) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            self.docker,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def bounded(stream):
            data = bytearray()
            while chunk := await stream.read(8192):
                data.extend(chunk)
                if len(data) > limit:
                    raise SandboxFailure("Docker response exceeds its size limit")
            return bytes(data)

        readers = [asyncio.create_task(bounded(s)) for s in (process.stdout, process.stderr)]
        try:
            stdout, stderr = await asyncio.gather(*readers)
            return await process.wait(), stdout, stderr
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            await process.communicate()

    async def _checked(self, *args: str, limit: int = 1024 * 1024) -> bytes:
        code, stdout, stderr = await self._cli(*args, limit=limit)
        if code:
            raise SandboxFailure(stderr.decode(errors="replace")[:2000] or "Docker call failed")
        return stdout

    async def check_image(self, image: str) -> None:
        validate_image(image)
        async with asyncio.timeout(10):
            code, _, stderr = await self._cli("image", "inspect", "--format", "{{.Id}}", image)
        if code:
            error = stderr.decode(errors="replace")
            if "No such image" in error:
                raise SandboxConfiguration("Sandbox image is not installed")
            raise SandboxFailure(error[:2000])

    async def cleanup_abandoned(self) -> None:
        """One service instance owns this label; never share it across live instances."""
        async with asyncio.timeout(10):
            output = await self._checked(
                "ps", "-aq", "--filter", f"label=durable-agent.tools.owner={self.owner}"
            )
            ids = output.decode().split()
            if ids:
                await self._checked("rm", "-f", *ids)

    async def _remove(self, name: str) -> None:
        async with asyncio.timeout(10):
            code, _, stderr = await self._cli("rm", "-f", name)
        if code and b"No such container" not in stderr:
            raise SandboxFailure("Could not remove sandbox: " + stderr.decode(errors="replace"))

    async def run(
        self,
        workspace: Path,
        command: str,
        cwd: str,
        config: SandboxConfig,
        deadline: float,
        *,
        timeout_seconds: int = 120,
        on_started: Callable[[str], Awaitable[None]] | None = None,
    ) -> ToolOutput:
        """Command time starts in the container; deadline bounds the whole attempt."""
        validate_image(config.image_digest)
        if not workspace.is_absolute() or "," in str(workspace):
            raise SandboxConfiguration("Workspace must be an absolute path without commas")
        name = f"durable-tools-{uuid4().hex}"
        source = await asyncio.to_thread(Path(supervisor.__file__).read_text)
        wall_deadline = time.time() + deadline - asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout_at(deadline):
                await self.check_image(config.image_digest)
                await self._checked(
                    "create",
                    f"--name={name}",
                    "--pull=never",
                    f"--label=durable-agent.tools.owner={self.owner}",
                    f"--user={self.uid}:{self.gid}",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--memory=512m",
                    "--memory-swap=512m",
                    "--cpus=1",
                    "--pids-limit=64",
                    f"--ulimit=fsize={config.workspace_max_bytes}",
                    "--tmpfs=/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                    f"--mount=type=bind,source={workspace},target=/workspace",
                    "--workdir=/workspace",
                    "--env=HOME=/tmp",
                    "--env=PYTHONDONTWRITEBYTECODE=1",
                    "--entrypoint=python",
                    config.image_digest,
                    "-I",
                    "-c",
                    source,
                    command,
                    cwd,
                    str(wall_deadline),
                    str(config.log_max_bytes),
                    str(timeout_seconds),
                )
                if on_started:
                    await self._checked("start", name)
                    await on_started(name)
                    output = await self._checked(
                        "logs", "--follow", name, limit=config.log_max_bytes * 6 + 8192
                    )
                else:
                    output = await self._checked(
                        "start", "--attach", name, limit=config.log_max_bytes * 6 + 8192
                    )
                state = json.loads(
                    await self._checked("inspect", "--format", "{{json .State}}", name)
                )
                if state["Running"] or state["OOMKilled"] or state["ExitCode"]:
                    raise SandboxFailure("Sandbox stopped without a completed command")
                try:
                    return TypeAdapter(ToolOutput).validate_json(output)
                except ValidationError as exc:
                    raise SandboxFailure("Sandbox returned an invalid result") from exc
        finally:
            cleanup = asyncio.create_task(self._remove(name))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
