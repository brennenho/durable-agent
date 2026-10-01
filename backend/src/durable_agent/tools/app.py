"""Tool API signatures. Application handlers are not implemented yet."""

from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from durable_agent.contracts import (
    ErrorResponse,
    ExecutionError,
    FileListing,
    ToolExecuteRequest,
    ToolExecuteResponse,
    WorkspaceVersion,
)

app = FastAPI(title="Durable Agent Tools", version="0.1.0")
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
    "/tools/execute",
    response_model=ToolExecuteResponse,
    operation_id="execute_tool",
    responses={409: {"model": ErrorResponse, "description": "Changed input or stale generation"}},
)
async def execute_tool(body: ToolExecuteRequest) -> ToolExecuteResponse:
    raise NotImplementedError("Tool execution is not implemented")


@routes.get(
    "/tools/workspace/initial",
    response_model=WorkspaceVersion,
    operation_id="get_initial_workspace",
)
async def get_initial_workspace() -> WorkspaceVersion:
    raise NotImplementedError("Workspace import is not implemented")


@routes.get(
    "/tools/workspace-versions/{version_id}/files",
    response_model=FileListing,
    operation_id="list_workspace_files",
    responses={404: {"model": ErrorResponse, "description": "Workspace version not found"}},
)
async def list_workspace_files(version_id: UUID) -> FileListing:
    raise NotImplementedError("Workspace file listing is not implemented")


@routes.get(
    "/tools/workspace-versions/{version_id}/file",
    response_class=Response,
    operation_id="download_workspace_file",
    responses={
        200: {
            "description": "Saved file bytes",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
            "headers": {"Content-Disposition": {"schema": {"type": "string"}}},
        },
        404: {"model": ErrorResponse, "description": "Workspace version or file not found"},
    },
)
async def download_workspace_file(version_id: UUID, path: str) -> Response:
    raise NotImplementedError("Workspace file downloads are not implemented")


app.include_router(routes)
