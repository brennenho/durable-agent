"""Accept reports under the run lock. The caller commits the entire transition."""

import json
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import (
    ExecutionError,
    FailedOutcome,
    ReportRequest,
    ReportResponse,
    SuccessfulOutcome,
)
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.control.leases import check_lease, lock_attempt
from durable_agent.control.progress import accept_result
from durable_agent.db.models import Attempt, Operation, Run


async def report_scope(session: AsyncSession, attempt_id: UUID) -> dict:
    row = (
        await session.execute(
            select(Operation.run_id, Attempt.operation_id)
            .join(Operation, Operation.id == Attempt.operation_id)
            .where(Attempt.id == attempt_id)
        )
    ).one()
    return {"run_id": row.run_id, "operation_id": row.operation_id}


def clear_lease(run: Run) -> None:
    run.lease_owner = run.lease_expires_at = None


def failed_attempt(run: Run, attempt: Attempt, error: ExecutionError, now: datetime) -> None:
    attempt.error, attempt.finished_at = error.model_dump(mode="json"), now
    clear_lease(run)
    if error.retryable and attempt.attempt_no < run.config["max_attempts"]:
        delay = error.retry_after_seconds
        if delay is None:
            delay = min(2 ** min(attempt.attempt_no - 1, 5), 30)
        try:
            retry_at = now + timedelta(seconds=delay)
        except OverflowError as exc:
            raise ControlError(
                409, "invalid_retry_delay", "Retry delay exceeds the supported timestamp range"
            ) from exc
        run.status, run.next_attempt_at = "retrying", retry_at
    else:
        run.status, run.next_attempt_at = "failed", None
        run.pause_requested = False
        run.terminal_error, run.finished_at = attempt.error, now


async def accept_report(
    session: AsyncSession,
    attempt_id: UUID,
    body: ReportRequest,
    *,
    agents: AgentRuntime | None = None,
) -> ReportResponse:
    attempt, operation, run = await lock_attempt(session, attempt_id)
    if attempt.worker_id != body.worker_id or attempt.lease_generation != body.lease_generation:
        raise ControlError(409, "stale_attempt", "Report does not belong to this attempt")
    outcome = body.outcome
    if attempt.status in ("accepted", "error"):
        recorded = (
            SuccessfulOutcome(
                status="succeeded",
                result=operation.result,
                provider_request_id=attempt.provider_request_id,
                usage=attempt.usage,
            )
            if attempt.status == "accepted"
            else FailedOutcome(
                status="failed",
                error=attempt.error,
                provider_request_id=attempt.provider_request_id,
            )
        )
        if json.dumps(recorded.model_dump(mode="json"), sort_keys=True) != json.dumps(
            outcome.model_dump(mode="json"), sort_keys=True
        ):
            raise ControlError(409, "report_conflict", "Attempt already has a different report")
        return ReportResponse(status="already_recorded")
    now = await session.scalar(select(func.clock_timestamp()))
    check_lease(attempt, operation, run, body, now)
    attempt.provider_request_id = outcome.provider_request_id
    if outcome.status == "failed":
        failed_attempt(run, attempt, outcome.error, now)
        attempt.status = "error"
    else:
        await accept_result(session, run, operation, outcome.result, now, agents=agents)
        attempt.status, attempt.finished_at, attempt.usage = "accepted", now, outcome.usage
    await session.flush()
    return ReportResponse(status="recorded")
