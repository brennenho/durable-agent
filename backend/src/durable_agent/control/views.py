"""Read persisted progress; no tool calls or commits."""

from datetime import datetime
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import (
    AgentConfig,
    AttemptView,
    ModelRequest,
    ModelResult,
    OperationInspection,
    OperationResult,
    OperationView,
    RunDetail,
    RunSummary,
    ToolRequest,
)
from durable_agent.control.errors import ControlError
from durable_agent.db.models import Attempt, Operation, Run
from durable_agent.harness.contracts import AgentState

RESULT = TypeAdapter(OperationResult)


def restore_state(run: Run, operations: list[Operation]) -> AgentState:
    history = [
        RESULT.validate_python(op.result) for op in operations if op.completed_at is not None
    ]
    pending = []
    turns = 0
    for result in history:
        if isinstance(result, ModelResult):
            if pending:
                raise ValueError("Stored model result precedes pending tool results")
            turns += 1
            pending = list(result.output.tool_calls)
        else:
            if not pending or pending[0].call_id != result.call_id:
                raise ValueError("Stored tool result is out of order")
            pending.pop(0)
    return AgentState(
        config=AgentConfig.model_validate(run.config),
        prompt=run.prompt,
        history=history,
        previous_turns=ModelRequest.model_validate(operations[0].request).previous_turns
        if operations
        else [],
        pending_tool_calls=pending,
        turn_count=turns,
    )


def workspace_version(state: AgentState) -> UUID:
    for result in reversed(state.history):
        if result.kind == "tool":
            return result.workspace_version_id
    return state.config.initial_workspace_version_id


def summary(run: Run, last_progress: datetime | None) -> RunSummary:
    return RunSummary(
        id=run.id,
        prompt=run.prompt,
        model=run.config["model"],
        status=run.status,
        created_at=run.created_at,
        finished_at=run.finished_at,
        next_attempt_at=run.next_attempt_at,
        last_heartbeat_at=run.last_heartbeat_at,
        last_progress_at=last_progress,
        terminal_error=run.terminal_error,
    )


async def list_summaries(session: AsyncSession) -> list[RunSummary]:
    rows = await session.execute(
        select(Run, func.max(Operation.completed_at))
        .outerjoin(Operation, Run.id == Operation.run_id)
        .group_by(Run.id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .execution_options(populate_existing=True)
    )
    return [summary(run, progress) for run, progress in rows]


async def load_operations(session: AsyncSession, run_id: UUID) -> list[Operation]:
    return list(
        await session.scalars(
            select(Operation)
            .where(Operation.run_id == run_id)
            .order_by(Operation.position)
            .execution_options(populate_existing=True)
        )
    )


async def inspect_operation(
    session: AsyncSession, run_id: UUID, operation_id: UUID
) -> OperationInspection:
    row = (
        await session.execute(
            select(Operation.kind, Operation.request, Run.config)
            .join(Run)
            .where(Operation.run_id == run_id, Operation.id == operation_id)
        )
    ).one_or_none()
    if row is None:
        raise ControlError(404, "operation_not_found", "Operation not found in this run")
    return OperationInspection(
        operation={"kind": row.kind, "request": row.request}, config=row.config
    )


def tool_summary(operation: Operation) -> dict[str, str | None]:
    if operation.kind != "tool":
        return {"tool_name": None, "tool_target": None}
    call = ToolRequest.model_validate(operation.request).call
    key = "command" if call.name == "run_command" else "path"
    target = call.arguments.get(key, "." if call.name == "list_files" else None)
    if isinstance(target, str):
        target = " ".join(target.split())
        target = target if len(target) <= 160 else target[:159] + "…"
    else:
        target = None
    return {
        "tool_name": call.name,
        "tool_target": target,
    }


async def detail(session: AsyncSession, run_id: UUID) -> RunDetail:
    # All progress writers lock this row. SHARE keeps this multi-query view consistent.
    run = await session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ControlError(404, "run_not_found", "Run not found")
    operations = await load_operations(session, run_id)
    attempts = await session.scalars(
        select(Attempt)
        .join(Operation)
        .where(Operation.run_id == run_id)
        .order_by(Operation.position, Attempt.attempt_no)
        .execution_options(populate_existing=True)
    )
    by_operation = {op.id: [] for op in operations}
    for attempt in attempts:
        by_operation[attempt.operation_id].append(
            AttemptView.model_validate(attempt, from_attributes=True)
        )
    state = restore_state(run, operations)
    final = state.history[-1] if state.history else None
    progress = max((op.completed_at for op in operations if op.completed_at), default=None)
    return RunDetail(
        **summary(run, progress).model_dump(),
        final_answer=final.output.text
        if run.status == "succeeded" and isinstance(final, ModelResult)
        else None,
        workspace_version_id=workspace_version(state),
        max_attempts=state.config.max_attempts,
        operations=[
            OperationView(
                id=op.id,
                position=op.position,
                kind=op.kind,
                created_at=op.created_at,
                completed_at=op.completed_at,
                result=op.result,
                attempts=by_operation[op.id],
                **tool_summary(op),
            )
            for op in operations
        ],
    )
