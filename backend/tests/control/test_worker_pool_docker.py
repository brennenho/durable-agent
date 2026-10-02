"""Opt-in Docker test: isolated containers run a signal-aware sleeper, never inference."""

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from durable_agent.control.worker_pool import NUMBER, PROJECT, SERVICE, DockerWorkers

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DOCKER_SOCKET"), reason="Set TEST_DOCKER_SOCKET for a real Docker test"
)


def test_real_docker_grows_adopts_and_drains_pool():
    async def exercise():
        project = f"worker-pool-test-{uuid4().hex[:12]}"
        transport = httpx.AsyncHTTPTransport(uds=os.environ["TEST_DOCKER_SOCKET"])
        async with httpx.AsyncClient(
            base_url="http://docker", transport=transport, timeout=10
        ) as client:
            network = await client.post("/v1.47/networks/create", json={"Name": project})
            network.raise_for_status()
            pool = DockerWorkers(client, project, 4)
            try:
                for slot in (1, 2):
                    response = await client.post(
                        "/v1.47/containers/create",
                        params={"name": f"{project}-worker-{slot}"},
                        json={
                            "Image": os.environ.get(
                                "TEST_WORKER_IMAGE", "durable-agent-backend:dev"
                            ),
                            "Cmd": [
                                "python",
                                "-c",
                                "import signal,sys,time; "
                                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); "
                                "time.sleep(3600)",
                            ],
                            "Labels": {PROJECT: project, SERVICE: "worker", NUMBER: str(slot)},
                            "HostConfig": {"NetworkMode": project},
                        },
                    )
                    response.raise_for_status()
                    started = await client.post(f"/v1.47/containers/{response.json()['Id']}/start")
                    started.raise_for_status()
                await pool.resize(4)
                before = await pool.inventory()
                assert len(before) == 4
                assert all(w["State"] == "running" for w in before)
                await DockerWorkers(client, project, 4).resize(4)
                assert {w["Id"] for w in await pool.inventory()} == {w["Id"] for w in before}
                # Give the test processes time to install their signal handlers.
                await asyncio.sleep(0.5)
                await pool.resize(2)
                after = await pool.inventory()
                assert {w["Labels"][NUMBER] for w in after} == {"1", "2"}
                assert all(w["State"] == "running" for w in after)
            finally:
                for worker in await pool.inventory():
                    removed = await client.delete(
                        f"/v1.47/containers/{worker['Id']}", params={"force": "true"}
                    )
                    removed.raise_for_status()
                removed = await client.delete(f"/v1.47/networks/{network.json()['Id']}")
                removed.raise_for_status()

    asyncio.run(exercise())
