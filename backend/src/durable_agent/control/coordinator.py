"""Find delegation work from saved rows; callers commit before emitting hooks."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from durable_agent.contracts import DelegateArguments, ToolRequest, ToolResult
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.delegation import create_children
from durable_agent.control.errors import ControlError
from durable_agent.control.progress import accept_result
from durable_agent.db.models import Operation, Run

REPORT_LIMIT = 16_384


async def join_children(
    session: AsyncSession, parent: Run, operation: Operation, *, agents: AgentRuntime | None = None
) -> None:
    request = ToolRequest.model_validate(operation.request)
    tasks = DelegateArguments.model_validate(request.call.arguments).tasks
    children = list(
        await session.scalars(
            select(Run).where(Run.parent_operation_id == operation.id).order_by(Run.child_index)
        )
    )
    if len(children) != len(tasks) or any(
        child.child_index != index or child.status not in ("succeeded", "failed")
        for index, child in enumerate(children)
    ):
        raise ValueError("Delegation does not have a complete terminal child batch")
    reports = []
    for child, task in zip(children, tasks, strict=True):
        report = None
        sdk_child = child.config.get("agent") is not None
        if child.status == "succeeded" and sdk_child:
            report = child.output if isinstance(child.output, str) else None
        elif child.status == "succeeded":
            final = await session.scalar(
                select(Operation.result)
                .where(
                    Operation.run_id == child.id,
                    Operation.kind == "model",
                    Operation.completed_at.is_not(None),
                )
                .order_by(Operation.position.desc())
                .limit(1)
            )
            report = final["output"]["text"]
        reports.append(
            {
                "run_id": str(child.id),
                "name": task.name,
                "status": child.status,
                "report": report[:REPORT_LIMIT] if report is not None else None,
                "error": child.terminal_error,
                "truncated": report is not None and len(report) > REPORT_LIMIT,
                **({"output": child.output} if sdk_child else {}),
            }
        )
    now = await session.scalar(select(func.clock_timestamp()))
    result = ToolResult(
        kind="tool",
        call_id=request.call.call_id,
        output={"status": "ok", "value": {"children": reports}},
        workspace_version_id=request.input_version_id,
    )
    await accept_result(session, parent, operation, result, now, agents=agents)


async def advance_delegations(
    session: AsyncSession, limit: int = 100, *, agents: AgentRuntime | None = None
) -> list[tuple[str, UUID, UUID]]:
    child = aliased(Run)
    unfinished = (
        select(child.id)
        .where(
            child.parent_operation_id == Operation.id,
            child.status.not_in(("succeeded", "failed")),
        )
        .exists()
    )
    # Excluding busy batches lets newer parents progress within the bounded scan.
    rows = await session.execute(
        select(Run, Operation)
        .join(Operation, Operation.run_id == Run.id)
        .where(
            Run.status == "waiting",
            Run.pause_requested.is_(False),
            Operation.completed_at.is_(None),
            ~unfinished,
        )
        .order_by(Run.created_at, Run.id)
        .with_for_update(of=Run, skip_locked=True)
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    events = []
    for parent, operation in rows:
        try:
            created = await create_children(session, parent, operation, agents=agents)
        except ControlError as exc:
            parent.status, parent.next_attempt_at, parent.pause_requested = "failed", None, False
            parent.finished_at = await session.scalar(select(func.clock_timestamp()))
            parent.terminal_error = exc.error.model_dump(mode="json")
            continue
        if created:
            hook = "control.after_children_commit"
        else:
            await join_children(session, parent, operation, agents=agents)
            hook = "control.after_join_commit"
        events.append((hook, parent.id, operation.id))
    await session.flush()
    return events
