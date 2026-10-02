import asyncio
import json
import sys

import pytest


@pytest.mark.parametrize("command_budget", [1, 120])
def test_sandbox_deadlines_survive_runner_process_exit(docker_runner, tmp_path, command_budget):
    runner, config = docker_runner
    script = """
import asyncio, sys
from pathlib import Path
from durable_agent.contracts import SandboxConfig
from durable_agent.tools.runner import DockerRunner
async def run():
    docker, owner, root, config, budget = sys.argv[1:]
    runner = DockerRunner(docker=docker, owner=owner)
    await runner.run(Path(root), 'printf started > started; sleep 30', '/workspace',
                     SandboxConfig.model_validate_json(config),
                     asyncio.get_running_loop().time() + 3, timeout_seconds=int(budget))
asyncio.run(run())
"""

    async def exercise():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            runner.docker,
            runner.owner,
            str(tmp_path),
            config.model_dump_json(),
            str(command_budget),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(5):
                while not (tmp_path / "started").exists():
                    if process.returncode is not None:
                        _, error = await process.communicate()
                        raise AssertionError(error.decode())
                    await asyncio.sleep(0.02)
            container = (
                (
                    await runner._checked(
                        "ps", "-q", "--filter", f"label=durable-agent.tools.owner={runner.owner}"
                    )
                )
                .decode()
                .strip()
            )
            assert container
            process.kill()
            await process.communicate()
            async with asyncio.timeout(5):
                while True:
                    state = json.loads(
                        await runner._checked("inspect", "--format", "{{json .State}}", container)
                    )
                    if not state["Running"]:
                        break
                    await asyncio.sleep(0.05)
            assert (state["ExitCode"] == 0) == (command_budget == 1)
            await runner.cleanup_abandoned()
            assert (await runner._cli("inspect", container))[0] != 0
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            await runner.cleanup_abandoned()

    asyncio.run(exercise())
