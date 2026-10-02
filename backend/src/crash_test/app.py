"""Authenticated controller API. Runs outside the application containers."""

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from durable_agent.crash_test import HookEvent, HookReply
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from crash_test.contracts import (
    AutoscalingSetting,
    ContainerAction,
    Session,
    Snapshot,
    StartScenario,
)
from crash_test.docker import Container, DockerControl, DockerError
from crash_test.room import ControlRoom


def create_app(room: ControlRoom | None = None, *, token: str | None = None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.token = token or os.environ["CRASH_TEST_HOOK_TOKEN"]
        if len(app.state.token) < 32:
            raise ValueError("Set a random CRASH_TEST_HOOK_TOKEN of at least 32 characters")
        async with httpx.AsyncClient(
            base_url=os.environ.get("CRASH_TEST_CONTROL_URL", "http://127.0.0.1:8000"), timeout=5
        ) as client:
            app.state.room = room or ControlRoom(
                DockerControl(
                    os.environ["CRASH_TEST_PROJECT"],
                    compose_directory=Path(os.environ["CRASH_TEST_COMPOSE_DIRECTORY"]),
                ),
                client,
                Path(os.environ.get("CRASH_TEST_LOG_DIR", ".crash-test/reports")),
            )
            try:
                yield
            finally:
                await app.state.room.close()

    async def authenticate(request: Request):
        expected = f"Bearer {request.app.state.token}"
        if not secrets.compare_digest(request.headers.get("authorization", ""), expected):
            raise HTTPException(401, "Crash Test token required")

    app = FastAPI(title="Crash Test", lifespan=lifespan, dependencies=[Depends(authenticate)])

    @app.exception_handler(DockerError)
    @app.exception_handler(OSError)
    async def unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/snapshot", response_model=Snapshot)
    async def snapshot(request: Request):
        return await request.app.state.room.snapshot()

    @app.post("/scenarios", response_model=Session, status_code=202)
    async def start(body: StartScenario, request: Request):
        return await request.app.state.room.start(body.scenario, body.run_id)

    @app.post("/scenarios/cancel", response_model=Session | None)
    async def cancel(request: Request):
        room = request.app.state.room
        await room.close()
        return room.current.snapshot() if room.current else None

    @app.post("/containers/action", response_model=Container)
    async def action(body: ContainerAction, request: Request):
        return await request.app.state.room.action(body)

    @app.post("/autoscaling", response_model=AutoscalingSetting)
    async def autoscaling(body: AutoscalingSetting, request: Request):
        return await request.app.state.room.set_autoscaling(body.enabled)

    @app.post("/hooks", response_model=HookReply)
    async def hook(body: HookEvent, request: Request):
        if body.observed_at.tzinfo is None:
            raise HTTPException(422, "Hook timestamp must include a timezone")
        if body.name == "control.before_scale" and body.service == "control":
            return request.app.state.room.autoscaling_hook()
        await request.app.state.room.observe_lifecycle(body)
        current = request.app.state.room.current
        if current and current.status == "running":
            return await current.observe_hook(body)
        return HookReply(action="continue")

    return app


app = create_app()
