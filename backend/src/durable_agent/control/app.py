"""Control HTTP routes. Queries and transactions live in the owning modules."""

from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from durable_agent.contracts import (
    Assignment,
    ClaimRequest,
    ErrorResponse,
    ExecutionError,
    FileListing,
    HeartbeatRequest,
    HeartbeatResponse,
    ReportRequest,
    ReportResponse,
    RunDetail,
    RunFileListing,
    RunSummary,
    SubmitRunRequest,
)
from durable_agent.control.errors import ControlError
from durable_agent.control.files import saved_version
from durable_agent.control.runs import create_run, find_submission
from durable_agent.control.runtime import DATABASE_UNAVAILABLE, Runtime, Settings, open_runtime
from durable_agent.control.views import detail, list_summaries

health = APIRouter()
routes = APIRouter(
    responses={
        501: {"model": ErrorResponse, "description": "Handler not implemented"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    }
)


async def not_implemented(request: Request, exc: NotImplementedError) -> JSONResponse:
    error = ExecutionError(code="not_implemented", message=str(exc), retryable=False)
    return JSONResponse(status_code=501, content=ErrorResponse(error=error).model_dump(mode="json"))


@health.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    raise HTTPException(503, "Service implementation has not started")


@routes.post(
    "/api/runs",
    response_model=RunSummary,
    operation_id="submit_run",
    responses={
        409: {"model": ErrorResponse, "description": "Submission key has a different prompt"}
    },
)
async def submit_run(body: SubmitRunRequest, request: Request) -> RunSummary:
    runtime = get_runtime(request)
    async with runtime.db.session() as session:
        if existing := await find_submission(session, body):
            return await detail(session, existing.id)
    config = await runtime.new_config()
    async with runtime.db.session() as session:
        run = await create_run(session, body, config)
        await session.commit()
        return await detail(session, run.id)


@routes.get("/api/runs", response_model=list[RunSummary], operation_id="list_runs")
async def list_runs(request: Request) -> list[RunSummary]:
    async with get_runtime(request).db.session() as session:
        return await list_summaries(session)


@routes.get(
    "/api/runs/{run_id}",
    response_model=RunDetail,
    operation_id="get_run",
    responses={404: {"model": ErrorResponse, "description": "Run not found"}},
)
async def get_run(run_id: UUID, request: Request) -> RunDetail:
    async with get_runtime(request).db.session() as session:
        return await detail(session, run_id)


@routes.get(
    "/api/runs/{run_id}/files",
    response_model=RunFileListing,
    operation_id="list_run_files",
    responses={404: {"model": ErrorResponse, "description": "Run not found"}},
)
async def list_run_files(run_id: UUID, request: Request) -> RunFileListing:
    runtime = get_runtime(request)
    async with runtime.db.session() as session:
        version = await saved_version(session, run_id)
    response = await runtime.tools_get(f"/tools/workspace-versions/{version}/files")
    try:
        listing = FileListing.model_validate_json(response.content)
    except ValidationError as exc:
        raise ControlError(
            503, "invalid_tool_response", "Tool service returned an invalid listing", retryable=True
        ) from exc
    return RunFileListing(workspace_version_id=version, entries=listing.entries)


@routes.get(
    "/api/runs/{run_id}/file",
    response_class=Response,
    operation_id="download_run_file",
    responses={
        200: {
            "description": "Saved file bytes",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
            "headers": {"Content-Disposition": {"schema": {"type": "string"}}},
        },
        404: {"model": ErrorResponse, "description": "Run, accepted version, or file not found"},
    },
)
async def download_run_file(
    run_id: UUID, version_id: UUID, path: str, request: Request
) -> Response:
    runtime = get_runtime(request)
    async with runtime.db.session() as session:
        version = await saved_version(session, run_id, version_id)
    response = await runtime.tools_get(
        f"/tools/workspace-versions/{version}/file", params={"path": path}
    )
    filename = quote(PurePosixPath(path).name, safe="")
    return Response(
        content=response.content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@routes.post("/internal/claims", response_model=Assignment | None, operation_id="claim_work")
async def claim_work(body: ClaimRequest) -> Assignment | None:
    raise NotImplementedError("Work claiming is not implemented")


@routes.post(
    "/internal/attempts/{attempt_id}/heartbeat",
    response_model=HeartbeatResponse,
    operation_id="heartbeat_attempt",
    responses={409: {"model": ErrorResponse, "description": "Expired or superseded attempt"}},
)
async def heartbeat_attempt(attempt_id: UUID, body: HeartbeatRequest) -> HeartbeatResponse:
    raise NotImplementedError("Attempt heartbeats are not implemented")


@routes.post(
    "/internal/attempts/{attempt_id}/report",
    response_model=ReportResponse,
    operation_id="report_attempt",
    responses={409: {"model": ErrorResponse, "description": "Stale attempt or conflicting report"}},
)
async def report_attempt(attempt_id: UUID, body: ReportRequest) -> ReportResponse:
    raise NotImplementedError("Attempt reports are not implemented")


def get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise ControlError(503, "not_ready", "Control is not ready", retryable=True)
    return runtime


async def control_error(request: Request, exc: ControlError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content=ErrorResponse(error=exc.error).model_dump(mode="json")
    )


async def database_error(request: Request, exc: Exception) -> JSONResponse:
    error = ExecutionError(
        code="database_unavailable", message="Database unavailable", retryable=True
    )
    return JSONResponse(status_code=503, content=ErrorResponse(error=error).model_dump(mode="json"))


def create_app(
    settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_runtime(
            settings or Settings.from_environment(), transport=transport
        ) as runtime:
            app.state.runtime = runtime
            try:
                yield
            finally:
                del app.state.runtime

    application = FastAPI(title="Durable Agent Control", version="0.1.0", lifespan=lifespan)
    application.add_exception_handler(NotImplementedError, not_implemented)
    application.add_exception_handler(ControlError, control_error)
    for exception in DATABASE_UNAVAILABLE:
        application.add_exception_handler(exception, database_error)
    application.include_router(health)
    application.include_router(routes)
    return application


app = create_app()
