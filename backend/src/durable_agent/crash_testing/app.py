"""Authenticated controller API. Runs outside the application containers."""

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from durable_agent.crash_test import HookEvent, HookReply
from durable_agent.crash_testing.contracts import ContainerAction, Session, Snapshot, StartScenario
from durable_agent.crash_testing.docker import Container, DockerControl, DockerError
from durable_agent.crash_testing.room import ControlRoom


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

    @app.post("/hooks", response_model=HookReply)
    async def hook(body: HookEvent, request: Request):
        if body.observed_at.tzinfo is None:
            raise HTTPException(422, "Hook timestamp must include a timezone")
        current = request.app.state.room.current
        if current and current.status == "running":
            return await current.observe_hook(body)
        return HookReply(action="continue")

    return app


app = create_app()
