import asyncio

import httpx
import pytest
from crash_test.app import create_app
from crash_test.room import ControlRoom
from durable_agent.crash_test import CrashTestHooks, HookInterrupted

from .test_runner import Docker


def test_authenticated_setting_controls_scaling_hooks_during_scenarios(tmp_path):
    async def exercise():
        async def blocked(request):
            await asyncio.Event().wait()

        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(blocked)
        ) as upstream:
            docker = Docker()
            docker.project = "crash-test-unit"
            room = ControlRoom(docker, upstream, tmp_path)
            app = create_app(room, token="t" * 32)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    base_url="http://room", transport=httpx.ASGITransport(app=app)
                ) as client,
            ):
                assert (
                    await client.post("/autoscaling", json={"enabled": False})
                ).status_code == 401
                client.headers["Authorization"] = "Bearer " + "t" * 32
                before = (await client.get("/snapshot")).json()
                assert before["autoscaling_enabled"] is True
                assert before["autoscaling_checked_at"] is None
                hooks = CrashTestHooks("control", client)
                await hooks.hit("control.before_scale")
                assert (await client.post("/autoscaling", json={})).status_code == 422
                assert (
                    await client.post("/scenarios", json={"scenario": "baseline"})
                ).status_code == 202
                response = await client.post("/autoscaling", json={"enabled": False})
                assert response.status_code == 200 and response.json() == {"enabled": False}
                with pytest.raises(HookInterrupted):
                    await hooks.hit("control.before_scale")
                paused = (await client.get("/snapshot")).json()
                assert paused["autoscaling_enabled"] is False
                assert paused["autoscaling_checked_at"] is not None
                assert paused["current"]["status"] == "running"
                # Scaling permission does not interrupt ordinary execution hooks.
                await hooks.hit("control.before_report_commit")
                assert (await client.post("/scenarios/cancel")).status_code == 200
                with pytest.raises(HookInterrupted):
                    await hooks.hit("control.before_scale")
                response = await client.post("/autoscaling", json={"enabled": True})
                assert response.json() == {"enabled": True}
                await hooks.hit("control.before_scale")
                assert [e["label"] for e in room.journal.events] == [
                    "Autoscaling paused",
                    "Autoscaling allowed",
                ]

    asyncio.run(exercise())


def test_failed_setting_change_does_not_report_success(tmp_path, monkeypatch):
    async def exercise():
        async with httpx.AsyncClient(base_url="http://control") as upstream:
            room = ControlRoom(Docker(), upstream, tmp_path)

            async def unavailable(*args, **kwargs):
                raise OSError("Cannot write controller journal")

            monkeypatch.setattr(room.journal, "record", unavailable)
            app = create_app(room, token="t" * 32)
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    base_url="http://room",
                    transport=httpx.ASGITransport(app=app),
                    headers={"Authorization": "Bearer " + "t" * 32},
                ) as client,
            ):
                response = await client.post("/autoscaling", json={"enabled": False})
                assert response.status_code == 503
                assert room.autoscaling_enabled is True

    asyncio.run(exercise())
