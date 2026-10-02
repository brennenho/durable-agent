"""Control HTTP routes. Queries and transactions live in the owning modules."""

from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID

import httpx
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text

from durable_agent.contracts import (
    Assignment,
    ClaimRequest,
    ConversationDetail,
    ConversationSummary,
    ErrorResponse,
    ExecutionError,
    FileListing,
    HeartbeatRequest,
    HeartbeatResponse,
    OperationInspection,
    ReportRequest,
    ReportResponse,
    RunDetail,
    RunFileListing,
    RunSummary,
    SubmitFollowUpRequest,
    SubmitForkRequest,
    SubmitRunRequest,
    ToolCheckpoint,
)
from durable_agent.control.continuations import create_continuation
from durable_agent.control.conversations import conversation_detail, list_conversations
from durable_agent.control.errors import ControlError
from durable_agent.control.files import saved_version
from durable_agent.control.forks import create_fork
from durable_agent.control.leases import claim, heartbeat
from durable_agent.control.pause import set_pause
from durable_agent.control.reports import accept_report, report_scope
from durable_agent.control.runs import create_run, find_submission
from durable_agent.control.runtime import DATABASE_UNAVAILABLE, Runtime, Settings, open_runtime
from durable_agent.control.views import detail, inspect_operation, list_summaries
from durable_agent.crash_test import HookInterrupted

health = APIRouter()
routes = APIRouter(
    responses={
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    }
)


@health.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/health/ready", tags=["health"])
async def readiness(request: Request) -> dict[str, str]:
    async with get_runtime(request).db.session() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@routes.post(
    "/api/runs",
    response_model=RunSummary,
    operation_id="submit_run",
    responses={
        409: {"model": ErrorResponse, "description": "Submission key has a different prompt"}
    },
)
async def submit_run(body: SubmitRunRequest, request: Request) -> RunSummary:
    if body.is_sdk_input:
        raise ControlError(501, "sdk_unavailable", "SDK execution is not installed yet")
    runtime = get_runtime(request)
    async with runtime.db.session() as session:
        if existing := await find_submission(session, body):
            return await detail(session, existing.id)
    config = await runtime.new_config()
    async with runtime.db.session() as session:
        run = await create_run(session, body, config)
        await session.commit()
        return await detail(session, run.id)


@routes.post(
    "/api/conversations",
    response_model=ConversationSummary,
    operation_id="create_conversation",
    responses={409: {"model": ErrorResponse, "description": "Conflicting submission key"}},
)
async def create_conversation(body: SubmitRunRequest, request: Request) -> ConversationSummary:
    run = await submit_run(body, request)
    async with get_runtime(request).db.session() as session:
        return await conversation_detail(session, run.id)


@routes.get(
    "/api/conversations",
    response_model=list[ConversationSummary],
    operation_id="list_conversations",
)
async def get_conversations(request: Request) -> list[ConversationSummary]:
    async with get_runtime(request).db.session() as session:
        return await list_conversations(session)


@routes.get(
    "/api/conversations/{conversation_id}",
    response_model=ConversationDetail,
    operation_id="get_conversation",
    responses={404: {"model": ErrorResponse, "description": "Conversation not found"}},
)
async def get_conversation(conversation_id: UUID, request: Request) -> ConversationDetail:
    async with get_runtime(request).db.session() as session:
        return await conversation_detail(session, conversation_id)


@routes.post(
    "/api/conversations/{conversation_id}/runs",
    response_model=RunSummary,
    operation_id="submit_follow_up",
    responses={
        404: {"model": ErrorResponse, "description": "Conversation not found"},
        409: {
            "model": ErrorResponse,
            "description": "Conflicting submission or conversation state",
        },
    },
)
async def submit_follow_up(
    conversation_id: UUID, body: SubmitFollowUpRequest, request: Request
) -> RunSummary:
    if body.is_sdk_input:
        raise ControlError(501, "sdk_unavailable", "SDK execution is not installed yet")
    async with get_runtime(request).db.session() as session:
        run = await create_continuation(session, conversation_id, body)
        await session.commit()
        return await detail(session, run.id)


@routes.post(
    "/api/runs/{run_id}/forks",
    response_model=ConversationSummary,
    operation_id="fork_run",
    responses={
        404: {"model": ErrorResponse, "description": "Source run or operation not found"},
        409: {
            "model": ErrorResponse,
            "description": "Conflicting submission or unavailable checkpoint",
        },
    },
)
async def fork_run(run_id: UUID, body: SubmitForkRequest, request: Request) -> ConversationSummary:
    if body.is_sdk_input:
        raise ControlError(501, "sdk_unavailable", "SDK execution is not installed yet")
    async with get_runtime(request).db.session() as session:
        run = await create_fork(session, run_id, body)
        await session.commit()
        return await conversation_detail(session, run.conversation_id)


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


async def change_pause(run_id: UUID, request: Request, paused: bool) -> RunSummary:
    async with get_runtime(request).db.session() as session:
        await set_pause(session, run_id, paused)
        await session.commit()
        return await detail(session, run_id)


@routes.post(
    "/api/runs/{run_id}/pause",
    response_model=RunSummary,
    operation_id="pause_run",
    responses={404: {"model": ErrorResponse, "description": "Run not found"}},
)
async def pause_run(run_id: UUID, request: Request) -> RunSummary:
    return await change_pause(run_id, request, True)


@routes.post(
    "/api/runs/{run_id}/resume",
    response_model=RunSummary,
    operation_id="resume_run",
    responses={404: {"model": ErrorResponse, "description": "Run not found"}},
)
async def resume_run(run_id: UUID, request: Request) -> RunSummary:
    return await change_pause(run_id, request, False)


@routes.get(
    "/api/runs/{run_id}/operations/{operation_id}",
    response_model=OperationInspection,
    operation_id="inspect_operation",
    responses={404: {"model": ErrorResponse, "description": "Operation not found in this run"}},
)
async def get_operation(run_id: UUID, operation_id: UUID, request: Request) -> OperationInspection:
    async with get_runtime(request).db.session() as session:
        return await inspect_operation(session, run_id, operation_id)


@routes.get(
    "/api/runs/{run_id}/operations/{operation_id}/checkpoint",
    response_model=ToolCheckpoint | None,
    operation_id="inspect_tool_checkpoint",
    responses={404: {"model": ErrorResponse, "description": "Operation not found in this run"}},
)
async def inspect_tool_checkpoint(
    run_id: UUID, operation_id: UUID, request: Request
) -> ToolCheckpoint | None:
    runtime = get_runtime(request)
    async with runtime.db.session() as session:
        inspection = await inspect_operation(session, run_id, operation_id)
    if inspection.operation.kind != "tool" or inspection.operation.request.call.name == "delegate":
        return None
    response = await runtime.tools_get(f"/tools/operations/{operation_id}/checkpoint")
    try:
        return TypeAdapter(ToolCheckpoint | None).validate_json(response.content)
    except ValidationError as exc:
        raise ControlError(
            503,
            "invalid_tool_response",
            "Tool service returned an invalid checkpoint",
            retryable=True,
        ) from exc


@routes.get(
    "/api/runs/{run_id}/files",
    response_model=RunFileListing,
    operation_id="list_run_files",
    responses={404: {"model": ErrorResponse, "description": "Run not found"}},
)
async def list_run_files(
    run_id: UUID, request: Request, version_id: UUID | None = None
) -> RunFileListing:
    runtime = get_runtime(request)
    async with runtime.db.session() as session:
        version = await saved_version(session, run_id, version_id)
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
async def claim_work(body: ClaimRequest, request: Request) -> Assignment | None:
    async with get_runtime(request).db.session() as session:
        assignment = await claim(session, body.worker_id, agents=get_runtime(request).agents)
        await session.commit()
        return assignment


@routes.post(
    "/internal/attempts/{attempt_id}/heartbeat",
    response_model=HeartbeatResponse,
    operation_id="heartbeat_attempt",
    responses={409: {"model": ErrorResponse, "description": "Expired or superseded attempt"}},
)
async def heartbeat_attempt(
    attempt_id: UUID, body: HeartbeatRequest, request: Request
) -> HeartbeatResponse:
    async with get_runtime(request).db.session() as session:
        response = await heartbeat(session, attempt_id, body)
        await session.commit()
        return response


@routes.post(
    "/internal/attempts/{attempt_id}/report",
    response_model=ReportResponse,
    operation_id="report_attempt",
    responses={409: {"model": ErrorResponse, "description": "Stale attempt or conflicting report"}},
)
async def report_attempt(attempt_id: UUID, body: ReportRequest, request: Request) -> ReportResponse:
    runtime = get_runtime(request)
    scope = None
    async with runtime.db.session() as session:
        response = await accept_report(session, attempt_id, body, agents=runtime.agents)
        if (
            runtime.hooks
            and runtime.hooks.client
            and response.status == "recorded"
            and body.outcome.status == "succeeded"
        ):
            scope = await report_scope(session, attempt_id)
        await session.commit()
    if scope:
        try:
            await runtime.hooks.hit(
                "control.after_accept_commit",
                **scope,
                attempt_id=attempt_id,
                worker_id=body.worker_id,
                lease_generation=body.lease_generation,
            )
        except HookInterrupted as exc:
            raise ControlError(503, "crash_test_interrupted", str(exc), retryable=True) from exc
    return response


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
    application.add_exception_handler(ControlError, control_error)
    for exception in DATABASE_UNAVAILABLE:
        application.add_exception_handler(exception, database_error)
    application.include_router(health)
    application.include_router(routes)
    return application


app = create_app()
