import asyncio
import os
from uuid import uuid4

import pytest
from durable_agent.tools.custom import execute_custom_tool
from durable_agent.tools.files import InvalidToolCall
from durable_agent.tools.runner import DockerRunner, SandboxFailure
from durable_agent.tools.workspaces import capture_initial_workspace, read_workspace_file

from .test_custom_invocation import custom_request


@pytest.fixture
def custom_sandbox(tmp_path):
    image = os.environ.get("SDK_SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("Set SDK_SANDBOX_TEST_IMAGE to an installed image with sdk_test_tools")
    runner = DockerRunner(
        docker=os.environ.get("DOCKER_BIN", "docker"), owner=f"test-{uuid4().hex}"
    )
    root = tmp_path / "attempts"
    root.mkdir()
    request = custom_request()
    request.definition = request.definition.model_copy(
        update={"entrypoint": "sdk_test_tools:save_note"}
    )
    request.sandbox = request.sandbox.model_copy(
        update={"image_digest": image, "log_max_bytes": 4096}
    )
    seed = capture_initial_workspace(None, 1024)
    return runner, root, request, seed


def test_custom_tool_saves_literal_arguments_and_completed_business_error(custom_sandbox):
    runner, root, request, seed = custom_sandbox
    literal = "héllo'; touch injected; $(echo wrong)"
    request.arguments = {"content": literal, "mode": "native"}

    async def exercise():
        deadline = asyncio.get_running_loop().time() + 30
        output, archive = await execute_custom_tool(seed, request, runner, root, deadline)
        assert output.value["content"] == literal
        assert output.value["attempt_id"] == str(request.attempt_id)
        assert read_workspace_file(archive, "note.txt", 1024).decode() == literal
        request.arguments = {"mode": "error"}
        output, archive = await execute_custom_tool(seed, request, runner, root, deadline)
        assert output.code == "needs_review"
        assert read_workspace_file(archive, "note.txt", 1024) == b"default"

    asyncio.run(exercise())
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("mode", ["exception", "invalid", "nan", "crash", "sleep"])
def test_failed_custom_attempts_discard_files_and_remove_containers(custom_sandbox, mode):
    runner, root, request, seed = custom_sandbox
    request.arguments = {"mode": mode}
    expected = {"crash": SandboxFailure, "sleep": TimeoutError}.get(mode, InvalidToolCall)

    async def exercise():
        deadline = asyncio.get_running_loop().time() + (2 if mode == "sleep" else 30)
        with pytest.raises(expected):
            await execute_custom_tool(seed, request, runner, root, deadline)
        containers = await runner._checked(
            "ps", "-aq", f"--filter=label=durable-agent.tools.owner={runner.owner}"
        )
        assert not containers.strip()

    asyncio.run(exercise())
    assert list(root.iterdir()) == []


def test_custom_result_limit_is_enforced_before_checkpoint(custom_sandbox):
    runner, root, request, seed = custom_sandbox
    request.sandbox = request.sandbox.model_copy(update={"log_max_bytes": 30})

    async def exercise():
        with pytest.raises(InvalidToolCall, match="exceeds log_max_bytes"):
            await execute_custom_tool(
                seed, request, runner, root, asyncio.get_running_loop().time() + 30
            )

    asyncio.run(exercise())
    assert list(root.iterdir()) == []
