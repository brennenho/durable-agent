import os
from uuid import uuid4

import pytest
from durable_agent.contracts import SandboxConfig
from durable_agent.tools.runner import DockerRunner


@pytest.fixture
def docker_runner(tmp_path):
    image = os.environ.get("SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("Set SANDBOX_TEST_IMAGE to an installed, pinned Python sandbox image")
    runner = DockerRunner(
        docker=os.environ.get("DOCKER_BIN", "docker"), owner=f"test-{uuid4().hex}"
    )
    if os.getuid() == 0:
        os.chown(tmp_path, runner.uid, runner.gid)
    config = SandboxConfig(image_digest=image, workspace_max_bytes=1024 * 1024, log_max_bytes=103)
    return runner, config
