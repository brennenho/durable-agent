import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from durable_agent.contracts import SandboxConfig
from durable_agent.tools.runner import DockerRunner, SandboxConfiguration, SandboxFailure


@pytest.fixture
def docker_runner():
    image = os.environ.get("SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("Set SANDBOX_TEST_IMAGE to an installed, pinned Python sandbox image")
    runner = DockerRunner(
        docker=os.environ.get("DOCKER_BIN", "docker"), owner=f"test-{uuid4().hex}"
    )
    config = SandboxConfig(image_digest=image, workspace_max_bytes=1024 * 1024, log_max_bytes=103)
    return runner, config


def execute(runner, config, root, command, seconds=10, cwd="/workspace"):
    async def run():
        return await runner.run(
            root, command, cwd, config, asyncio.get_running_loop().time() + seconds
        )

    return asyncio.run(run())


def test_command_runs_nonroot_offline_and_without_host_secrets(
    docker_runner, tmp_path, monkeypatch
):
    runner, config = docker_runner
    monkeypatch.setenv("TOOLS_TEST_SECRET", "must-not-enter-sandbox")
    command = (
        'test $(id -u) != 0 && test -z "$TOOLS_TEST_SECRET" && '
        "test ! -e /var/run/docker.sock && test ! -w /etc && "
        "test $(wc -l < /proc/net/route) = 1 && "
        "printf saved > file && printf stdout && printf stderr >&2 && exit 7"
    )
    result = execute(runner, config, tmp_path, command)
    assert result.value == {
        "exit_code": 7,
        "stdout": "stdout",
        "stderr": "stderr",
        "truncated": False,
    }
    assert (tmp_path / "file").read_text() == "saved"
    assert (
        asyncio.run(
            runner._checked(
                "ps", "-aq", "--filter", f"label=durable-agent.tools.owner={runner.owner}"
            )
        )
        == b""
    )


def test_logs_are_bounded_and_background_writers_stop(docker_runner, tmp_path):
    runner, config = docker_runner
    result = execute(
        runner, config, tmp_path, "(sleep 1; touch late) & python -c 'print(\"x\" * 1000000)'"
    )
    assert result.value["truncated"] is True
    assert len(result.value["stdout"].encode()) <= 103
    asyncio.run(asyncio.sleep(1.1))
    assert not (tmp_path / "late").exists()


def test_timeout_has_no_result(docker_runner, tmp_path):
    runner, config = docker_runner
    with pytest.raises((TimeoutError, SandboxFailure)):
        execute(runner, config, tmp_path, "printf partial > file; sleep 10", seconds=0.5)
    # The caller must discard this private tree, regardless of whether execution started.
    assert (
        asyncio.run(
            runner._checked(
                "ps", "-aq", "--filter", f"label=durable-agent.tools.owner={runner.owner}"
            )
        )
        == b""
    )


@pytest.mark.parametrize("failure", ["crash", "cancel"])
def test_interrupted_container_is_removed(docker_runner, tmp_path, failure):
    runner, config = docker_runner

    async def run():
        task = asyncio.create_task(
            runner.run(
                tmp_path,
                "printf partial > partial; sleep 30",
                "/workspace",
                config,
                asyncio.get_running_loop().time() + 15,
            )
        )
        try:
            async with asyncio.timeout(5):
                while not (tmp_path / "partial").exists():
                    await asyncio.sleep(0.02)
            ids = (
                (
                    await runner._checked(
                        "ps", "-q", "--filter", f"label=durable-agent.tools.owner={runner.owner}"
                    )
                )
                .decode()
                .split()
            )
            assert len(ids) == 1
            if failure == "crash":
                await runner._checked("kill", ids[0])
            else:
                task.cancel()
            with pytest.raises(SandboxFailure if failure == "crash" else asyncio.CancelledError):
                await task
            assert (await runner._cli("inspect", ids[0]))[0] != 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_cleanup_is_scoped_to_its_owner(docker_runner):
    runner, config = docker_runner

    async def run():
        owned = (
            (
                await runner._checked(
                    "create",
                    "--label",
                    f"durable-agent.tools.owner={runner.owner}",
                    config.image_digest,
                )
            )
            .decode()
            .strip()
        )
        other = (
            (
                await runner._checked(
                    "create",
                    "--label",
                    f"durable-agent.tools.owner=other-{runner.owner}",
                    config.image_digest,
                )
            )
            .decode()
            .strip()
        )
        try:
            await runner.cleanup_abandoned()
            assert (await runner._cli("inspect", owned))[0] != 0
            assert (await runner._cli("inspect", other))[0] == 0
        finally:
            await runner._remove(owned)
            await runner._remove(other)

    asyncio.run(run())


def test_mutable_image_rejected_before_launch():
    config = SandboxConfig(
        image_digest="python:latest", workspace_max_bytes=1024, log_max_bytes=100
    )
    with pytest.raises(SandboxConfiguration, match="pinned"):
        execute(
            DockerRunner(docker="not-an-executable"), config, Path("/private/workspace"), "true"
        )
