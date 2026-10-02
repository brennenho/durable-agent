import asyncio
import os
from uuid import uuid4

import pytest
from crash_test.docker import DockerControl, DockerError


@pytest.mark.parametrize("project", ["", "durable-agent", "-project", "../project"])
def test_existing_projects_require_repository_binding(project):
    with pytest.raises(ValueError):
        DockerControl(project)


@pytest.mark.parametrize("existing", [False, True])
def test_real_kill_restart_scope_and_stale_target(tmp_path, existing):
    image = os.environ.get("SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("Set SANDBOX_TEST_IMAGE for real Docker control checks")

    async def exercise():
        driver = DockerControl(
            ("dev-" if existing else "crash-test-") + uuid4().hex[:12],
            compose_directory=tmp_path if existing else None,
        )
        ids = []
        try:
            for project, opt_in in [
                (driver.project, "true"),
                (driver.project, "false"),
                ("other", "true"),
            ]:
                directory = tmp_path if opt_in == "true" else "/elsewhere"
                container_id = (
                    await driver.command(
                        "run",
                        "-d",
                        "--restart=unless-stopped",
                        "--label",
                        f"com.docker.compose.project={project}",
                        "--label",
                        "com.docker.compose.service=tools",
                        "--label",
                        f"durable-agent.crash-test={'false' if existing else opt_in}",
                        "--label",
                        f"com.docker.compose.project.working_dir={directory}",
                        "--label",
                        f"com.docker.compose.project.config_files={tmp_path}/compose.yaml",
                        image,
                        "python",
                        "-c",
                        "import time; time.sleep(600)",
                    )
                ).strip()
                ids.append(container_id)
            inventory = await driver.inventory()
            assert [item.id for item in inventory] == [ids[0]]
            for foreign in ids[1:]:
                with pytest.raises(DockerError, match="outside"):
                    await driver.inspect(foreign)
            original = inventory[0]
            stopped = await driver.kill(original)
            assert not stopped.running and stopped.exit_code == 137
            restarted = await driver.start(stopped)
            assert restarted.running and restarted.started_at != original.started_at
            with pytest.raises(DockerError, match="restarted"):
                await driver.kill(original)
            assert (await driver.inspect(original.id)).running
            stopped = await driver.kill(restarted)
            assert not (await driver.inspect(stopped.id)).running
            inspect = driver.inspect

            async def removed_during_inspection(container_id):
                if original.id.startswith(container_id):
                    await driver.command("rm", "-f", original.id)
                    ids.remove(original.id)
                return await inspect(container_id)

            driver.inspect = removed_during_inspection
            assert await driver.inventory() == []
        finally:
            for container_id in ids:
                await driver.command("rm", "-f", container_id)

    asyncio.run(exercise())
