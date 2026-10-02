"""Control containers belonging to the selected repository and Compose project."""

import asyncio
import json
import re
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

SERVICES = {"control", "tools", "worker", "frontend", "db"}
OPT_IN = "durable-agent.crash-test"


class DockerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Container:
    id: str
    name: str
    service: str
    instance: str
    running: bool
    started_at: str
    exit_code: int
    health: str

    def json(self):
        return asdict(self)


class DockerControl:
    def __init__(
        self, project: str, *, compose_directory: Path | None = None, docker: str = "docker"
    ):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
            raise ValueError("Select a valid Compose project name")
        if compose_directory is None and not project.startswith("crash-test-"):
            raise ValueError("Existing projects require their repository's Compose directory")
        self.compose_directory = str(compose_directory.resolve()) if compose_directory else None
        self.project, self.docker = project, docker

    async def command(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            self.docker, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        async def read(stream):
            chunks = bytearray()
            while chunk := await stream.read(8192):
                chunks.extend(chunk)
                if len(chunks) > 1024 * 1024:
                    raise DockerError("Docker response exceeded its limit")
            return chunks.decode(errors="replace")

        readers = [asyncio.create_task(read(s)) for s in (process.stdout, process.stderr)]
        try:
            async with asyncio.timeout(15):
                out, error = await asyncio.gather(*readers)
                if await process.wait():
                    raise DockerError(error[:1000] or "Docker command failed")
                return out
        except TimeoutError as exc:
            raise DockerError("Docker command timed out") from exc
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            await process.communicate()

    async def inspect(self, container_id: str) -> Container:
        # Never retrieve environment variables: they can contain application credentials.
        raw = json.loads(
            await self.command(
                "inspect",
                "--format",
                (
                    '{"id":{{json .Id}},"name":{{json .Name}},"instance":{{json .Config.Hostname}},'
                    '"labels":{{json .Config.Labels}},"state":{{json .State}}}'
                ),
                container_id,
            )
        )
        labels = raw["labels"] or {}
        service = labels.get("com.docker.compose.service")
        opted_in = labels.get(OPT_IN) == "true"
        repository_match = (
            self.compose_directory is not None
            and labels.get("com.docker.compose.project.working_dir") == self.compose_directory
            and f"{self.compose_directory}/compose.yaml"
            in labels.get("com.docker.compose.project.config_files", "").split(",")
        )
        owned_service = (
            labels.get("com.docker.compose.project") == self.project
            and (repository_match if self.compose_directory else opted_in)
            and service in SERVICES
        )
        owned_sandbox = labels.get("durable-agent.tools.owner") == self.project
        if not owned_service and not owned_sandbox:
            raise DockerError("Container is outside this Crash Test project")
        state = raw["state"]
        return Container(
            raw["id"],
            raw["name"].lstrip("/"),
            service if owned_service else "sandbox",
            raw["instance"],
            state["Running"],
            state["StartedAt"],
            state["ExitCode"],
            state.get("Health", {}).get("Status", "none"),
        )

    async def inventory(self) -> list[Container]:
        services, sandboxes = await asyncio.gather(
            self.command(
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--filter",
                f"label=com.docker.compose.project.working_dir={self.compose_directory}"
                if self.compose_directory
                else f"label={OPT_IN}=true",
            ),
            self.command(
                "ps", "-aq", "--filter", f"label=durable-agent.tools.owner={self.project}"
            ),
        )
        ids = sorted(set((services + sandboxes).split()))
        if len(ids) > 64:
            raise DockerError("Too many containers in the Crash Test project")
        results = []
        for container_id in ids:
            try:
                results.append(await self.inspect(container_id))
            except DockerError as exc:
                if str(exc) == "Container is outside this Crash Test project":
                    continue
                # Tools removes short-lived sandboxes between listing and inspection.
                # Suppress only a verified disappearance; daemon failures still surface.
                if await self.command("ps", "-aq", "--filter", f"id={container_id}"):
                    raise
        return results

    async def kill(self, target: Container) -> Container:
        current = await self.inspect(target.id)
        if not current.running or current.started_at != target.started_at:
            raise DockerError("Target stopped or restarted since it was selected")
        # Explicit SIGKILL, with manual-stop semantics so Compose cannot auto-restart it.
        await self.command("stop", "--signal", "SIGKILL", "--timeout", "0", current.id)
        stopped = await self.inspect(current.id)
        if stopped.running or stopped.exit_code != 137:
            raise DockerError("Could not verify that SIGKILL stopped the target")
        return stopped

    async def start(self, target: Container) -> Container:
        current = await self.inspect(target.id)
        if current.running or current.started_at != target.started_at:
            raise DockerError("Target changed since it was selected")
        if current.service == "sandbox":
            raise DockerError(
                "Sandbox recovery belongs to Tools; do not restart a partial workspace"
            )
        await self.command("start", current.id)
        started = await self.inspect(current.id)
        if not started.running:
            raise DockerError("Container did not start")
        return started
