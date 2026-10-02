"""Resize this Compose project's workers through the local Docker Engine."""

import json
import logging

import httpx

from durable_agent.crash_test import CrashTestHooks

logger = logging.getLogger(__name__)
PROJECT = "com.docker.compose.project"
SERVICE = "com.docker.compose.service"
NUMBER = "com.docker.compose.container-number"
MANAGED = "durable-agent.worker-pool"
STOP_SECONDS = 1000  # Longest command (900s), checkpoint/report budgets, and an in-flight claim.


class WorkerPoolError(RuntimeError):
    pass


class DockerWorkers:
    def __init__(
        self,
        client: httpx.AsyncClient,
        project: str,
        maximum: int,
        *,
        hooks: CrashTestHooks | None = None,
    ):
        if maximum < 2:
            raise ValueError("Worker maximum must be at least two")
        self.client, self.project, self.maximum = client, project, maximum
        self.hooks = hooks

    async def _observed(self, action: str, container_id: str, slot: int):
        logger.info("Worker %s %s; container: %s", slot, action, container_id)
        if self.hooks:
            await self.hooks.hit(
                f"control.worker_{action}", container_id=container_id, worker_slot=slot
            )

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = await self.client.request(method, f"/v1.47{path}", **kwargs)
        if response.is_error:
            # Docker responses can contain container configuration, including credentials.
            raise WorkerPoolError(f"Docker {method} {path} returned {response.status_code}")
        return response

    async def inventory(self) -> list[dict]:
        filters = {"label": [f"{PROJECT}={self.project}", f"{SERVICE}=worker"]}
        response = await self._request(
            "GET", "/containers/json", params={"all": "true", "filters": json.dumps(filters)}
        )
        workers = response.json()
        for worker in workers:
            labels = worker["Labels"]
            if labels.get(PROJECT) != self.project or labels.get(SERVICE) != "worker":
                raise WorkerPoolError("Docker returned a worker outside this project")
        return workers

    async def resize(self, desired: int) -> None:
        if not 2 <= desired <= self.maximum:
            raise ValueError("Desired workers must be between two and the configured maximum")
        workers = await self.inventory()
        slots = {}
        for worker in workers:
            slot = int(worker["Labels"][NUMBER])
            if slot in slots:
                raise WorkerPoolError("Multiple containers occupy the same worker slot")
            slots[slot] = worker
        # Compose creates the two baseline workers after Control becomes ready.
        if not {1, 2}.issubset(slots):
            raise WorkerPoolError("Waiting for Compose's two baseline workers")
        # Repair stopped baseline capacity before retiring any surplus workers.
        for slot, worker in slots.items():
            if slot <= desired and worker["State"] in {"created", "exited"}:
                response = await self._request("POST", f"/containers/{worker['Id']}/start")
                if response.status_code == 204:
                    action = "restarted" if worker["State"] == "exited" else "started"
                    await self._observed(action, worker["Id"], slot)
        for slot, worker in sorted(slots.items(), reverse=True):
            if slot <= desired or worker["Labels"].get(MANAGED) != self.project:
                continue
            if worker["State"] != "exited":
                await self._request(
                    "POST",
                    f"/containers/{worker['Id']}/stop",
                    params={"t": STOP_SECONDS},
                    timeout=STOP_SECONDS + 5,
                )
            await self._request("DELETE", f"/containers/{worker['Id']}")
            await self._observed("removed", worker["Id"], slot)
            del slots[slot]
        for slot in range(1, desired + 1):
            if slot in slots:
                continue
            if len(slots) >= self.maximum:
                break
            template = (await self._request("GET", f"/containers/{slots[1]['Id']}/json")).json()
            config, host = template["Config"], template["HostConfig"]
            labels = {**config["Labels"], NUMBER: str(slot), MANAGED: self.project}
            payload = {
                "Image": template["Image"],
                "Env": config["Env"],
                "Cmd": config["Cmd"],
                "Entrypoint": config["Entrypoint"],
                "WorkingDir": config["WorkingDir"],
                "User": config["User"],
                "Labels": labels,
                "HostConfig": {
                    "Binds": host["Binds"],
                    "NetworkMode": host["NetworkMode"],
                    "RestartPolicy": {"Name": "unless-stopped"},
                    "ExtraHosts": host["ExtraHosts"],
                },
                "NetworkingConfig": {
                    "EndpointsConfig": {
                        name: {} for name in template["NetworkSettings"]["Networks"]
                    }
                },
            }
            name = f"{self.project}-worker-{slot}"
            response = await self.client.post(
                "/v1.47/containers/create", params={"name": name}, json=payload
            )
            # A previous request may have created the slot but lost its acknowledgment.
            # Re-list on the next pass; never pick another name and exceed the cap.
            if response.status_code == 409:
                raise WorkerPoolError("Worker slot already exists; reconcile again before creating")
            if response.is_error:
                raise WorkerPoolError(f"Docker create returned {response.status_code}")
            container_id = response.json()["Id"]
            await self._observed("created", container_id, slot)
            response = await self._request("POST", f"/containers/{container_id}/start")
            if response.status_code == 204:
                await self._observed("started", container_id, slot)
            slots[slot] = {"Id": container_id}
