"""Tool deduplication and generation fencing; callers commit these transactions."""

import json

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import ToolExecuteRequest, ToolExecuteResponse, ToolOutput
from durable_agent.db.models import ToolRequest
from durable_agent.tools.workspaces import save_workspace


class ToolConflict(ValueError):
    pass


class ToolDeadlineExceeded(TimeoutError):
    pass


def _input(request: ToolExecuteRequest) -> dict:
    fields = {"name", "arguments", "sandbox"}
    if request.definition is not None:
        fields.update({"definition", "run_id"})
    return request.model_dump(mode="json", include=fields)


async def _locked(session: AsyncSession, request: ToolExecuteRequest) -> ToolRequest:
    row = await session.scalar(
        select(ToolRequest)
        .where(ToolRequest.operation_key == request.operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ToolConflict("Tool operation was not registered")
    if row.input_version_id != request.input_version_id or json.dumps(
        row.request, sort_keys=True
    ) != json.dumps(_input(request), sort_keys=True):
        raise ToolConflict("Operation ID was already used with different input")
    if request.lease_generation < row.lease_generation:
        raise ToolConflict("Attempt generation was superseded")
    return row


def _completed(row: ToolRequest) -> ToolExecuteResponse | None:
    if row.completed_at is None:
        return None
    return ToolExecuteResponse(output=row.result, workspace_version_id=row.output_version_id)


async def _check_deadline(session: AsyncSession, request: ToolExecuteRequest):
    now = await session.scalar(select(func.clock_timestamp()))
    if now >= request.deadline_at:
        raise ToolDeadlineExceeded("Tool execution deadline passed")
    return now


async def register_tool(
    session: AsyncSession, request: ToolExecuteRequest
) -> ToolExecuteResponse | None:
    await session.execute(
        insert(ToolRequest)
        .values(
            operation_key=request.operation_id,
            input_version_id=request.input_version_id,
            request=_input(request),
            lease_generation=request.lease_generation,
        )
        .on_conflict_do_nothing(index_elements=[ToolRequest.operation_key])
    )
    row = await _locked(session, request)
    if completed := _completed(row):
        return completed
    await _check_deadline(session, request)
    row.lease_generation = request.lease_generation
    return None


async def publish_tool(
    session: AsyncSession,
    request: ToolExecuteRequest,
    output: ToolOutput,
    archive: bytes | None,
) -> ToolExecuteResponse:
    """Publish a validated checkpoint, or reuse the input for a read-only result."""
    row = await _locked(session, request)
    if completed := _completed(row):
        return completed
    if row.lease_generation != request.lease_generation:
        raise ToolConflict("Attempt generation was not registered")
    now = await _check_deadline(session, request)
    version_id = request.input_version_id
    if archive is not None:
        version_id = await save_workspace(session, archive)
    row.result = output.model_dump(mode="json")
    row.output_version_id = version_id
    row.completed_at = now
    await session.flush()
    return ToolExecuteResponse(output=output, workspace_version_id=version_id)
