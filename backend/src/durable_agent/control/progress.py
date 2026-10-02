"""Save an operation result and the next harness step in the caller's transaction."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import OperationResult, ToolRequest
from durable_agent.control.errors import ControlError
from durable_agent.control.views import load_operations, restore_state, workspace_version
from durable_agent.db.models import Operation, Run
from durable_agent.harness import advance


async def accept_result(
    session: AsyncSession,
    run: Run,
    operation: Operation,
    result: OperationResult,
    now: datetime,
) -> None:
    """Caller holds the run lock and validates the execution's right to publish."""
    if operation.completed_at is not None:
        raise ControlError(409, "operation_complete", "Operation already has an accepted result")
    if result.kind != operation.kind or (
        result.kind == "tool" and result.call_id != operation.request["call"]["call_id"]
    ):
        raise ControlError(409, "invalid_result", "Result does not match the assigned operation")
    state = restore_state(run, await load_operations(session, run.id))
    transition = advance(state, result)
    operation.result, operation.completed_at = result.model_dump(mode="json"), now
    run.lease_owner = run.lease_expires_at = None
    if transition.status == "continue":
        next_op = transition.next
        version = result.workspace_version_id if result.kind == "tool" else workspace_version(state)
        request = (
            next_op.request
            if next_op.kind == "model"
            else ToolRequest(call=next_op.call, input_version_id=version)
        )
        session.add(
            Operation(
                run_id=run.id,
                position=operation.position + 1,
                kind=next_op.kind,
                request=request.model_dump(mode="json"),
            )
        )
        run.status, run.next_attempt_at = "queued", now
    else:
        run.status = "succeeded" if transition.status == "finished" else "failed"
        run.finished_at, run.next_attempt_at = now, None
        run.pause_requested = False
        run.terminal_error = (
            transition.error.model_dump(mode="json") if transition.status == "failed" else None
        )
