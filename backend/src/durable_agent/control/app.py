"""Control API signatures. Application handlers are not implemented yet."""

from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from durable_agent.contracts import (
    Assignment,
    ClaimRequest,
    ErrorResponse,
    ExecutionError,
    HeartbeatRequest,
    HeartbeatResponse,
    ReportRequest,
    ReportResponse,
    RunDetail,
    RunFileListing,
    RunSummary,
    SubmitRunRequest,
)

app = FastAPI(title="Durable Agent Control", version="0.1.0")
routes = APIRouter(
    responses={
        501: {"model": ErrorResponse, "description": "Handler not implemented"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    }
)


@app.exception_handler(NotImplementedError)
async def not_implemented(request: Request, exc: NotImplementedError) -> JSONResponse:
    error = ExecutionError(code="not_implemented", message=str(exc), retryable=False)
    return JSONResponse(status_code=501, content=ErrorResponse(error=error).model_dump(mode="json"))


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
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
async def submit_run(body: SubmitRunRequest) -> RunSummary:
    raise NotImplementedError("Run submission is not implemented")


@routes.get("/api/runs", response_model=list[RunSummary], operation_id="list_runs")
async def list_runs() -> list[RunSummary]:
    raise NotImplementedError("Run listing is not implemented")


@routes.get(
    "/api/runs/{run_id}",
    response_model=RunDetail,
    operation_id="get_run",
    responses={404: {"model": ErrorResponse, "description": "Run not found"}},
)
async def get_run(run_id: UUID) -> RunDetail:
    raise NotImplementedError("Run retrieval is not implemented")


@routes.get(
    "/api/runs/{run_id}/files",
    response_model=RunFileListing,
    operation_id="list_run_files",
    responses={404: {"model": ErrorResponse, "description": "Run not found"}},
)
async def list_run_files(run_id: UUID) -> RunFileListing:
    raise NotImplementedError("Run file listing is not implemented")


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
async def download_run_file(run_id: UUID, version_id: UUID, path: str) -> Response:
    raise NotImplementedError("Run file downloads are not implemented")


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


app.include_router(routes)
