import asyncio

import pytest
from durable_agent.contracts import SandboxConfig
from durable_agent.tools.archives import InvalidWorkspace, capture_workspace, unpack_workspace
from durable_agent.tools.commands import (
    CommandArgs,
    InvalidCommand,
    execute_command_tool,
    prepare_workspace_root,
)
from durable_agent.tools.runner import DockerRunner, SandboxConfiguration, SandboxFailure
from durable_agent.tools.workspaces import read_workspace_file


@pytest.fixture
def saved_input(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "keep").write_text("committed")
    (source / "delete").write_text("remove me")
    pool = tmp_path / "sandboxes"
    pool.mkdir()
    return capture_workspace(source, 1024), pool


def execute(saved_input, docker_runner, arguments, seconds=10):
    archive, pool = saved_input
    runner, config = docker_runner

    async def run():
        return await execute_command_tool(
            archive, arguments, config, runner, pool, asyncio.get_running_loop().time() + seconds
        )

    try:
        return asyncio.run(run())
    finally:
        assert list(pool.iterdir()) == []


def test_nonzero_command_saves_complete_files_and_permissions(saved_input, docker_runner):
    output, updated = execute(
        saved_input,
        docker_runner,
        {
            "command": (
                "mkdir nested; printf saved > nested/.new; "
                "chmod 000 nested/.new nested; rm delete; exit 7"
            )
        },
    )
    assert output.value["exit_code"] == 7
    assert read_workspace_file(updated, "keep", 1024) == b"committed"
    assert read_workspace_file(updated, "nested/.new", 1024) == b"saved"
    entries = {member.name: member for member, _ in unpack_workspace(updated, 1024)}
    assert entries["nested"].mode == entries["nested/.new"].mode == 0
    assert "delete" not in entries
    # Starting another execution from the same saved input never sees private prior edits.
    _, retried = execute(saved_input, docker_runner, {"command": "test ! -e nested; cat keep"})
    assert read_workspace_file(retried, "delete", 1024) == b"remove me"


@pytest.mark.parametrize(
    "arguments",
    [{"command": "touch nope", "cwd": "missing"}, {"command": "true", "cwd": "../escape"}],
)
def test_cwd_errors_reuse_input(saved_input, docker_runner, arguments):
    output, updated = execute(saved_input, docker_runner, arguments)
    assert output.status == "error"
    assert updated is None


@pytest.mark.parametrize(
    "command",
    [
        "ln -s /etc/passwd link",
        "mkfifo pipe",
        "dd if=/dev/zero of=one bs=1024 count=700; cp one two",
    ],
)
def test_invalid_checkpoints_never_return_a_completion(saved_input, docker_runner, command):
    with pytest.raises(InvalidWorkspace):
        execute(saved_input, docker_runner, {"command": command})


def test_timeout_discards_partial_edits_and_retry_restores_saved_input(saved_input, docker_runner):
    with pytest.raises((TimeoutError, SandboxFailure)):
        execute(
            saved_input, docker_runner, {"command": "printf partial > keep; sleep 30"}, seconds=1
        )
    output, _ = execute(saved_input, docker_runner, {"command": "cat keep"})
    assert output.value["stdout"] == "committed"


@pytest.mark.parametrize(
    "arguments",
    [{}, {"command": 123}, {"command": "\0"}, {"command": "true", "extra": 1}]
    + [{"command": "true", "timeout_seconds": value} for value in (0, 901, 1.5, True, "30")],
)
def test_invalid_command_schema_fails_before_launch(saved_input, arguments):
    runner = DockerRunner(docker="must-not-launch")
    config = SandboxConfig(image_digest="sha256:test", workspace_max_bytes=1024, log_max_bytes=100)
    with pytest.raises(InvalidCommand):
        execute(saved_input, (runner, config), arguments)


@pytest.mark.parametrize(
    "arguments,expected", [({}, 120), ({"timeout_seconds": 1}, 1), ({"timeout_seconds": 900}, 900)]
)
def test_command_budget_defaults_and_bounds(arguments, expected):
    assert CommandArgs.model_validate({"command": "true", **arguments}).timeout_seconds == expected


def test_command_budget_starts_after_setup(saved_input, docker_runner, monkeypatch):
    runner, _ = docker_runner
    check_image = runner.check_image

    async def slow_setup(image):
        await asyncio.sleep(1.2)
        await check_image(image)

    monkeypatch.setattr(runner, "check_image", slow_setup)
    output, _ = execute(
        saved_input, docker_runner, {"command": "sleep 0.2; cat keep", "timeout_seconds": 1}
    )
    assert output.value["stdout"] == "committed"


def test_command_timeout_discards_partial_files(saved_input, docker_runner):
    output, updated = execute(
        saved_input,
        docker_runner,
        {"command": "printf partial > keep; sleep 5", "timeout_seconds": 1},
    )
    assert output.code == "command_timeout"
    assert updated is None
    assert "discarded" in output.message
    output, _ = execute(saved_input, docker_runner, {"command": "cat keep"})
    assert output.value["stdout"] == "committed"


def test_startup_cleanup_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o750)
    (outside / "keep").write_text("saved")
    root = tmp_path / "pool"
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SandboxConfiguration, match="symlink"):
        prepare_workspace_root(root)
    root.unlink()
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    prepare_workspace_root(root)
    assert list(root.iterdir()) == []
    assert root.stat().st_mode & 0o777 == 0o700
    assert (outside / "keep").read_text() == "saved"
    assert outside.stat().st_mode & 0o777 == 0o750
