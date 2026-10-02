import asyncio

import httpx
from crash_test.app import create_app
from crash_test.room import ControlRoom

from .test_runner import Docker


def test_api_authentication_stale_selection_and_exclusive_scenarios(tmp_path):
    async def exercise():
        docker = Docker()
        docker.project = "crash-test-unit"

        async def blocked(request):
            await asyncio.Event().wait()

        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(blocked)
        ) as upstream:
            room = ControlRoom(docker, upstream, tmp_path)
            app = create_app(room, token="t" * 32)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    base_url="http://room", transport=httpx.ASGITransport(app=app)
                ) as client:
                    assert (await client.get("/snapshot")).status_code == 401
                    assert (await client.post("/containers/action", json={})).status_code == 401
                    client.headers["Authorization"] = "Bearer " + "t" * 32
                    snapshot = (await client.get("/snapshot")).json()
                    assert snapshot["project"] == docker.project and len(snapshot["services"]) == 5
                    body = {"id": "worker", "started_at": "old", "action": "kill"}
                    assert (await client.post("/containers/action", json=body)).status_code == 409
                    body["started_at"] = docker.containers["worker"].started_at
                    assert (await client.post("/containers/action", json=body)).status_code == 200
                    assert not docker.containers["worker"].running
                    body["action"] = "start"
                    assert (await client.post("/containers/action", json=body)).status_code == 200
                    assert (
                        await client.post("/scenarios", json={"scenario": "missing"})
                    ).status_code == 422
                    assert (
                        await client.post("/scenarios", json={"scenario": "baseline"})
                    ).status_code == 202
                    assert room.current.armed
                    assert (
                        await client.post("/scenarios", json={"scenario": "baseline"})
                    ).status_code == 409
                    body.update(action="kill", started_at=docker.containers["worker"].started_at)
                    assert (await client.post("/containers/action", json=body)).status_code == 200
                    assert not docker.containers["worker"].running and room.current.armed
                    body["action"] = "start"
                    assert (await client.post("/containers/action", json=body)).status_code == 200
                    current = (await client.get("/snapshot")).json()["current"]
                    assert current["manual_intervention"]
                    assert len([e for e in current["events"] if e["kind"] == "manual_action"]) == 2
                    response = await client.post(
                        "/hooks",
                        json={
                            "name": "tools.after_commit",
                            "service": "tools",
                            "instance": "host",
                            "pid": 1,
                            "observed_at": "2026-01-01T00:00:00",
                        },
                    )
                    assert response.status_code == 422
                    assert (await client.post("/scenarios/cancel")).status_code == 200
                    assert (await client.get("/snapshot")).json()["current"][
                        "status"
                    ] == "inconclusive"

    asyncio.run(exercise())


def test_start_acknowledges_preflight_and_cancel_cannot_change_a_new_session(tmp_path):
    async def exercise():
        docker = Docker()
        entered, release = asyncio.Event(), asyncio.Event()
        inventory = docker.inventory

        async def delayed_inventory():
            entered.set()
            await release.wait()
            return await inventory()

        docker.inventory = delayed_inventory
        async with httpx.AsyncClient(base_url="http://control") as client:
            room = ControlRoom(docker, client, tmp_path)
            starting = asyncio.create_task(room.start("baseline"))
            await entered.wait()
            assert not starting.done() and not room.current.armed
            release.set()
            response = await starting
            assert response["status"] == "running" and room.current.armed
            await asyncio.gather(room.close(), room.close())
            assert room.current.status == "inconclusive"
            await room.start("baseline")
            assert room.current.armed and room.current.status == "running"
            await room.close()

    asyncio.run(exercise())
