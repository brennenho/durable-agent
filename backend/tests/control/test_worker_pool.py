import asyncio
import json

import httpx
import pytest
from durable_agent.control.worker_pool import (
    MANAGED,
    NUMBER,
    PROJECT,
    SERVICE,
    DockerWorkers,
    WorkerPoolError,
)


class Engine:
    def __init__(self):
        self.workers = {
            str(n): {
                "Id": str(n),
                "Labels": {PROJECT: "test", SERVICE: "worker", NUMBER: str(n)},
                "State": "running",
            }
            for n in (1, 2)
        }
        self.requests = []
        self.created = []
        self.lose_create_ack = False
        self.create_conflict = False

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1.47")
        if path == "/containers/json":
            assert json.loads(request.url.params["filters"]) == {
                "label": [f"{PROJECT}=test", f"{SERVICE}=worker"]
            }
            return httpx.Response(200, json=list(self.workers.values()))
        if path == "/containers/create":
            payload = json.loads(request.content)
            self.created.append(payload)
            slot = payload["Labels"][NUMBER]
            assert request.url.params["name"] == f"test-worker-{slot}"
            self.workers[slot] = {"Id": slot, "Labels": payload["Labels"], "State": "created"}
            if self.create_conflict:
                self.create_conflict = False
                return httpx.Response(409)
            if self.lose_create_ack:
                self.lose_create_ack = False
                raise httpx.ReadTimeout("Create acknowledgment lost")
            return httpx.Response(201, json={"Id": slot})
        slot = path.split("/")[2]
        worker = self.workers[slot]
        if path.endswith("/json"):
            return httpx.Response(
                200,
                json={
                    "Image": "sha256:fixed-worker-image",
                    "Config": {
                        "Labels": worker["Labels"],
                        "Env": ["OPENAI_API_KEY=test-key"],
                        "Cmd": ["python", "-m", "durable_agent.worker"],
                        "Entrypoint": None,
                        "User": "",
                        "WorkingDir": "/app/backend",
                    },
                    "HostConfig": {
                        "Binds": ["/checkout/backend/src:/app/backend/src:ro"],
                        "NetworkMode": "test_default",
                        "ExtraHosts": None,
                    },
                    "NetworkSettings": {"Networks": {"test_default": {}}},
                },
            )
        if path.endswith("/start"):
            worker["State"] = "running"
        elif path.endswith("/stop"):
            assert int(request.url.params["t"]) >= 965
            worker["State"] = "exited"
        elif request.method == "DELETE":
            assert worker["State"] == "exited"
            del self.workers[slot]
        else:
            raise AssertionError(path)
        return httpx.Response(204)


def test_resize_reuses_slots_after_control_restart_and_only_retires_managed_extras():
    async def exercise():
        engine = Engine()
        async with httpx.AsyncClient(
            base_url="http://docker", transport=httpx.MockTransport(engine.handle)
        ) as client:
            await DockerWorkers(client, "test", 4).resize(4)
            await DockerWorkers(client, "test", 4).resize(4)
            assert len(engine.created) == 2
            assert len(engine.workers) == 4
            payload = engine.created[0]
            assert payload["Image"] == "sha256:fixed-worker-image"
            assert payload["Env"] == ["OPENAI_API_KEY=test-key"]
            assert payload["HostConfig"]["Binds"] == ["/checkout/backend/src:/app/backend/src:ro"]
            # A manually added worker is counted toward the cap but never removed.
            del engine.workers["4"]["Labels"][MANAGED]
            await DockerWorkers(client, "test", 4).resize(2)
            assert set(engine.workers) == {"1", "2", "4"}
            assert all(w["State"] == "running" for w in engine.workers.values())

    asyncio.run(exercise())


@pytest.mark.parametrize("conflict", [False, True])
def test_lost_create_ack_or_name_conflict_does_not_create_an_extra_worker(conflict):
    async def exercise():
        engine = Engine()
        engine.lose_create_ack, engine.create_conflict = not conflict, conflict
        async with httpx.AsyncClient(
            base_url="http://docker", transport=httpx.MockTransport(engine.handle)
        ) as client:
            with pytest.raises(WorkerPoolError if conflict else httpx.ReadTimeout):
                await DockerWorkers(client, "test", 4).resize(4)
            assert engine.workers["3"]["State"] == "created"
            await DockerWorkers(client, "test", 4).resize(4)
            assert len(engine.created) == 2
            assert all(w["State"] == "running" for w in engine.workers.values())

    asyncio.run(exercise())


def test_stopped_baseline_is_restarted_before_extra_workers_stop():
    async def exercise():
        engine = Engine()
        async with httpx.AsyncClient(
            base_url="http://docker", transport=httpx.MockTransport(engine.handle)
        ) as client:
            pool = DockerWorkers(client, "test", 4)
            await pool.resize(4)
            engine.workers["1"]["State"] = "exited"
            engine.requests.clear()
            await pool.resize(2)
            mutations = [r.url.path for r in engine.requests if r.method != "GET"]
            assert mutations[0] == "/v1.47/containers/1/start"
            assert set(engine.workers) == {"1", "2"}

    asyncio.run(exercise())


@pytest.mark.parametrize("desired", [1, 5])
def test_invalid_capacity_never_reaches_docker(desired):
    async def exercise():
        engine = Engine()
        async with httpx.AsyncClient(
            base_url="http://docker", transport=httpx.MockTransport(engine.handle)
        ) as client:
            with pytest.raises(ValueError):
                await DockerWorkers(client, "test", 4).resize(desired)
            assert not engine.requests

    asyncio.run(exercise())


def test_foreign_worker_response_is_rejected_before_mutation():
    async def exercise():
        engine = Engine()
        engine.workers["1"]["Labels"][PROJECT] = "other"
        async with httpx.AsyncClient(
            base_url="http://docker", transport=httpx.MockTransport(engine.handle)
        ) as client:
            with pytest.raises(WorkerPoolError, match="outside this project"):
                await DockerWorkers(client, "test", 4).resize(4)
            assert all(r.method == "GET" for r in engine.requests)

    asyncio.run(exercise())
