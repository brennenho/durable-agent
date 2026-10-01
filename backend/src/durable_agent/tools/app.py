"""Tools HTTP boundary and service-owned database lifetime."""

import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from durable_agent.contracts import (
    ErrorResponse,
    ExecutionError,
    FileListing,
    ToolExecuteRequest,
    ToolExecuteResponse,
    WorkspaceVersion,
)
from durable_agent.db.session import Database
from durable_agent.tools.archives import InvalidWorkspace
from durable_agent.tools.commands import InvalidCommand, filesystem, prepare_workspace_root
from durable_agent.tools.execution import execute_operation
from durable_agent.tools.files import InvalidToolCall
from durable_agent.tools.publication import ToolConflict
from durable_agent.tools.runner import DockerRunner, SandboxConfiguration, SandboxFailure
from durable_agent.tools.workspaces import (
    WorkspaceNotFound,
    capture_initial_workspace,
    list_workspace,
    load_workspace,
    read_workspace_file,
    save_workspace,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(os.environ["DATABASE_URL"])
    app.state.runner = None
    app.state.workspace_root = None
    try:
        app.state.max_bytes = int(os.environ.get("WORKSPACE_MAX_BYTES", 20 * 1024 * 1024))
        if app.state.max_bytes <= 0:
            raise ValueError("WORKSPACE_MAX_BYTES must be positive")
        source = os.environ.get("WORKSPACE_INPUT_DIR")
        archive = await asyncio.to_thread(
            capture_initial_workspace,
            Path(source) if source is not None else None,
            app.state.max_bytes,
        )
        root = os.environ.get("SANDBOX_WORKSPACE_ROOT")
        image = os.environ.get("SANDBOX_IMAGE_DIGEST")
        if root is not None or image is not None:
            if not root or not image or not Path(root).is_absolute() or "," in root:
                raise SandboxConfiguration(
                    "Set SANDBOX_IMAGE_DIGEST and an absolute SANDBOX_WORKSPACE_ROOT without commas"
                )
            owner = os.environ.get("SANDBOX_OWNER", "durable-agent-tools")
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", owner):
                raise SandboxConfiguration("SANDBOX_OWNER must be a short path-safe identifier")
            runner = DockerRunner(docker=os.environ.get("DOCKER_BIN", "docker"), owner=owner)
            await runner.cleanup_abandoned()
            await runner.check_image(image)
            app.state.workspace_root = Path(root) / f"tools-{owner}"
            await filesystem(prepare_workspace_root, app.state.workspace_root)
            app.state.runner = runner
            app.state.image = image
        async with db.session() as session:
            app.state.initial_version = await save_workspace(session, archive)
            await session.commit()
        app.state.database = db
        app.state.slots = asyncio.Semaphore(4)
        yield
    finally:
        app.state.runner = None
        app.state.database = None
        await db.close()


app = FastAPI(title="Durable Agent Tools", version="0.1.0", lifespan=lifespan)
routes = APIRouter(
    responses={
        501: {"model": ErrorResponse, "description": "Handler not implemented"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    }
)


ERRORS = {
    InvalidWorkspace: (400, "invalid_workspace", False),
    InvalidToolCall: (400, "invalid_tool_call", False),
    InvalidCommand: (400, "invalid_tool_call", False),
    SandboxConfiguration: (400, "sandbox_configuration", False),
    SandboxFailure: (503, "sandbox_unavailable", True),
    WorkspaceNotFound: (404, "workspace_not_found", False),
    FileNotFoundError: (404, "file_not_found", False),
    ToolConflict: (409, "tool_conflict", False),
    TimeoutError: (504, "deadline_exceeded", True),
    SQLAlchemyError: (503, "database_unavailable", True),
    OSError: (503, "tool_unavailable", True),
}


async def execution_error(request: Request, exc: Exception) -> JSONResponse:
    status, code, retryable = next(value for kind, value in ERRORS.items() if isinstance(exc, kind))
    message = "Database unavailable" if isinstance(exc, SQLAlchemyError) else str(exc)
    error = ExecutionError(code=code, message=message or code, retryable=retryable)
    return JSONResponse(
        status_code=status, content=ErrorResponse(error=error).model_dump(mode="json")
    )


for error_type in ERRORS:
    app.add_exception_handler(error_type, execution_error)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness(request: Request) -> dict[str, str]:
    if request.app.state.runner is None:
        raise HTTPException(503, "Sandbox is not configured")
    try:
        async with asyncio.timeout(3):
            async with request.app.state.database.session() as session:
                await session.execute(select(1))
            await request.app.state.runner.check_image(request.app.state.image)
    except (SQLAlchemyError, OSError, TimeoutError, SandboxFailure, SandboxConfiguration) as exc:
        raise HTTPException(503, "Tools dependencies are unavailable") from exc
    return {"status": "ok"}


@routes.post(
    "/tools/execute",
    response_model=ToolExecuteResponse,
    operation_id="execute_tool",
    responses={409: {"model": ErrorResponse, "description": "Changed input or stale generation"}},
)
async def execute_tool(body: ToolExecuteRequest, request: Request) -> ToolExecuteResponse:
    if body.sandbox.workspace_max_bytes > request.app.state.max_bytes:
        raise InvalidToolCall("Requested workspace limit exceeds the service limit")
    return await execute_operation(
        request.app.state.database,
        body,
        request.app.state.slots,
        runner=request.app.state.runner,
        workspace_root=request.app.state.workspace_root,
    )


@routes.get(
    "/tools/workspace/initial",
    response_model=WorkspaceVersion,
    operation_id="get_initial_workspace",
)
async def get_initial_workspace(request: Request) -> WorkspaceVersion:
    return WorkspaceVersion(workspace_version_id=request.app.state.initial_version)


async def saved_archive(request: Request, version_id: UUID) -> bytes:
    async with request.app.state.database.session() as session:
        return await load_workspace(session, version_id)


@routes.get(
    "/tools/workspace-versions/{version_id}/files",
    response_model=FileListing,
    operation_id="list_workspace_files",
    responses={404: {"model": ErrorResponse, "description": "Workspace version not found"}},
)
async def list_workspace_files(version_id: UUID, request: Request) -> FileListing:
    archive = await saved_archive(request, version_id)
    return await asyncio.to_thread(list_workspace, archive, request.app.state.max_bytes)


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
async def download_workspace_file(version_id: UUID, path: str, request: Request) -> Response:
    archive = await saved_archive(request, version_id)
    content = await asyncio.to_thread(
        read_workspace_file, archive, path, request.app.state.max_bytes
    )
    filename = quote(Path(path).name, safe="")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        },
    )


app.include_router(routes)
