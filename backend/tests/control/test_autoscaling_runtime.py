import asyncio
from uuid import uuid4

import httpx
import pytest
from durable_agent.control import autoscaling
from durable_agent.control.app import create_app
from durable_agent.control.worker_pool import NUMBER, DockerWorkers

from .test_recovery import expire
from .test_runtime import settings
from .test_worker_http import model_result
from .test_worker_pool import Engine


@pytest.mark.parametrize("failure", ["missing_label", "unexpected"])
def test_control_serves_and_recovers_work_during_scaling_failure(
    postgres_engine, monkeypatch, caplog, failure
):
    async def exercise():
        engine = Engine()
        retried, stopped = asyncio.Event(), asyncio.Event()
        broken = True
        observations = 0

        def docker_response(request):
            nonlocal observations
            if broken:
                observations += 1
                if observations >= 3:
                    retried.set()
                if failure == "unexpected":
                    raise RuntimeError("Unexpected Docker adapter failure")
                del engine.workers["2"]["Labels"][NUMBER]
                try:
                    return engine.handle(request)
                finally:
                    engine.workers["2"]["Labels"][NUMBER] = "2"
            return engine.handle(request)

        async def pool(db, project, maximum, hooks):
            async with httpx.AsyncClient(
                base_url="http://docker", transport=httpx.MockTransport(docker_response)
            ) as docker:
                try:
                    await autoscaling.scale_workers(
                        db, DockerWorkers(docker, project, maximum), hooks=hooks, interval=0.01
                    )
                finally:
                    stopped.set()

        monkeypatch.setattr("durable_agent.control.runtime.run_worker_pool", pool)
        configured = settings(postgres_engine)
        configured.worker_autoscale, configured.worker_project = True, "test"
        app = create_app(
            configured,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"workspace_version_id": str(uuid4())})
            ),
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(base_url="http://control", transport=httpx.ASGITransport(app)) as api,
        ):
            await asyncio.wait_for(retried.wait(), 3)
            expected_log = (
                "Worker scaling unavailable; retrying"
                if failure == "missing_label"
                else "Unexpected worker scaling failure; retrying"
            )
            assert any(
                record.message == expected_log and record.exc_info for record in caplog.records
            )
            assert (await api.get("/health/ready")).status_code == 200
            for _ in range(5):
                response = await api.post(
                    "/api/runs", json={"submission_key": str(uuid4()), "prompt": "Finish"}
                )
                assert response.status_code == 200
            worker = {"worker_id": str(uuid4())}
            claimed = await api.post("/internal/claims", json=worker)
            assert claimed.status_code == 200
            assignment = claimed.json()
            path = f"/internal/attempts/{assignment['attempt_id']}"
            lease = {**worker, "lease_generation": assignment["lease_generation"]}
            assert (await api.post(path + "/heartbeat", json=lease)).status_code == 200
            reported = await api.post(
                path + "/report",
                json={**lease, "outcome": {"status": "succeeded", "result": model_result()}},
            )
            assert reported.json() == {"status": "recorded"}
            assignment = (await api.post("/internal/claims", json=worker)).json()
            await expire(app.state.runtime.db)
            async with asyncio.timeout(3):
                while True:
                    saved = (await api.get(f"/api/runs/{assignment['run_id']}")).json()
                    if saved["status"] == "retrying":
                        break
                    await asyncio.sleep(0.01)
            assert all(r.method == "GET" for r in engine.requests)
            assert set(engine.workers) == {"1", "2"}

            broken = False
            async with asyncio.timeout(3):
                while len(engine.workers) < 3:
                    await asyncio.sleep(0.01)
            assert (await api.get("/health/ready")).status_code == 200
        assert stopped.is_set()

    asyncio.run(exercise())
