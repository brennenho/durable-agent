import asyncio

import pytest
from durable_agent.tools.archives import InvalidWorkspace, capture_workspace, unpack_workspace
from durable_agent.tools.commands import InvalidCommand, execute_command_tool
from durable_agent.tools.runner import SandboxFailure
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
    "arguments", [{}, {"command": 123}, {"command": "\0"}, {"command": "true", "extra": 1}]
)
def test_invalid_command_schema_fails_before_launch(saved_input, docker_runner, arguments):
    with pytest.raises(InvalidCommand):
        execute(saved_input, docker_runner, arguments)
