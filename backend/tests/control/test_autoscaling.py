import asyncio

import httpx
import pytest
from durable_agent.control import autoscaling
from durable_agent.control.worker_pool import DockerWorkers
from durable_agent.crash_test import CrashTestHooks
from durable_agent.db.models import Run
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from .test_leases import queued
from .test_worker_pool import Engine


def test_hook_blocks_docker_changes_until_scaling_is_allowed(database, config):
    async def exercise():
        engine = Engine()
        checked, allowed = asyncio.Event(), asyncio.Event()

        def hook(request):
            checked.set()
            return httpx.Response(
                200, json={"action": "continue" if allowed.is_set() else "interrupt"}
            )

        async with (
            database() as db,
            httpx.AsyncClient(
                base_url="http://crash", transport=httpx.MockTransport(hook)
            ) as client,
            httpx.AsyncClient(
                base_url="http://docker", transport=httpx.MockTransport(engine.handle)
            ) as docker,
        ):
            for _ in range(4):
                await queued(db, config)
            task = asyncio.create_task(
                autoscaling.scale_workers(
                    db,
                    DockerWorkers(docker, "test", 4),
                    hooks=CrashTestHooks("control", client),
                    interval=0.001,
                )
            )
            try:
                await asyncio.wait_for(checked.wait(), 1)
                assert not engine.requests
                allowed.set()
                async with asyncio.timeout(3):
                    while len(engine.workers) != 4:
                        await asyncio.sleep(0.001)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(exercise())


def test_loop_grows_to_cap_then_returns_to_baseline_after_pausing(database, config):
    async def exercise():
        engine = Engine()
        async with database() as db:
            for _ in range(6):
                await queued(db, config)
            async with httpx.AsyncClient(
                base_url="http://docker", transport=httpx.MockTransport(engine.handle)
            ) as client:
                task = asyncio.create_task(
                    autoscaling.scale_workers(
                        db, DockerWorkers(client, "test", 4), interval=0.001, down_delay=0
                    )
                )
                try:
                    async with asyncio.timeout(3):
                        while len(engine.workers) != 4:
                            await asyncio.sleep(0.001)
                        async with db.session() as session:
                            await session.execute(update(Run).values(pause_requested=True))
                            await session.commit()
                        while len(engine.workers) != 2:
                            await asyncio.sleep(0.001)
                    assert len(engine.created) == 2
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task

    asyncio.run(exercise())


@pytest.mark.parametrize("outage", ["database", "docker"])
def test_scaling_retries_outage_without_losing_the_existing_pool(database, monkeypatch, outage):
    async def exercise():
        engine = Engine()
        recovered = asyncio.Event()
        attempts = 0

        async def demand(session):
            nonlocal attempts
            attempts += 1
            if outage == "database" and attempts == 1:
                raise OperationalError("query", {}, OSError("unavailable"))
            return 4

        def handle(request):
            if outage == "docker" and attempts == 1:
                return httpx.Response(503)
            result = engine.handle(request)
            if len(engine.workers) == 4:
                recovered.set()
            return result

        monkeypatch.setattr(autoscaling, "worker_demand", demand)
        async with (
            database() as db,
            httpx.AsyncClient(
                base_url="http://docker", transport=httpx.MockTransport(handle)
            ) as client,
        ):
            task = asyncio.create_task(
                autoscaling.scale_workers(db, DockerWorkers(client, "test", 4), interval=0.001)
            )
            try:
                await asyncio.wait_for(recovered.wait(), 3)
                assert attempts >= 2
                assert len(engine.workers) == 4
                assert not any(r.method == "DELETE" for r in engine.requests)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(exercise())
