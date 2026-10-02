"""Lease queries. Lock the run and use database time; callers commit explicitly."""

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import (
    AgentConfig,
    Assignment,
    HeartbeatRequest,
    HeartbeatResponse,
    RunCommandArguments,
)
from durable_agent.contracts import Operation as WireOperation
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.db.models import Attempt, Operation, Run

LEASE = timedelta(seconds=30)
OPERATION = TypeAdapter(WireOperation)


def _attempt_deadline(started_at: datetime, operation: Operation, run: Run) -> datetime:
    seconds = run.config["call_timeout_seconds"]
    if operation.kind == "tool" and operation.request["call"]["name"] == "run_command":
        arguments = RunCommandArguments.model_validate(operation.request["call"]["arguments"])
        # Reserve time for setup, cleanup, checkpoint publication, and reporting.
        seconds = arguments.timeout_seconds + 30
    seconds = operation.request.get("timeout_seconds") or seconds
    return started_at + timedelta(seconds=seconds)


async def claim(
    session: AsyncSession, worker_id: UUID, *, agents: AgentRuntime | None = None
) -> Assignment | None:
    run = await session.scalar(
        select(Run)
        .where(
            Run.status.in_(["queued", "retrying"]),
            Run.pause_requested.is_(False),
            Run.next_attempt_at <= func.clock_timestamp(),
        )
        .order_by(Run.next_attempt_at, Run.created_at, Run.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if run is None:
        return None
    operation = await session.scalar(
        select(Operation).where(Operation.run_id == run.id, Operation.completed_at.is_(None))
    )
    if operation is None:
        raise ValueError("Claimable run has no pending operation")
    config = AgentConfig.model_validate(run.config)
    if config.agent is not None:
        try:
            (agents or AgentRuntime()).check(config)
        except ControlError as exc:
            run.status, run.next_attempt_at, run.pause_requested = "failed", None, False
            run.finished_at = await session.scalar(select(func.clock_timestamp()))
            run.terminal_error = exc.error.model_dump(mode="json")
            return None
    count = await session.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.operation_id == operation.id)
    )
    if count >= config.max_attempts:
        raise ValueError("Claimable operation has exhausted its attempt budget")
    now = await session.scalar(select(func.clock_timestamp()))
    try:
        assigned_operation = OPERATION.validate_python(
            {"kind": operation.kind, "request": operation.request}
        )
        deadline = _attempt_deadline(now, operation, run)
    except (ValueError, KeyError, TypeError):
        run.status, run.next_attempt_at, run.pause_requested = "failed", None, False
        run.finished_at = now
        run.terminal_error = {
            "code": "invalid_operation",
            "message": "Saved operation is invalid",
            "retryable": False,
        }
        return None
    run.status, run.next_attempt_at = "running", None
    run.lease_owner = worker_id
    run.lease_generation += 1
    run.lease_expires_at = min(now + LEASE, deadline)
    run.last_heartbeat_at = now
    attempt = Attempt(
        operation_id=operation.id,
        attempt_no=count + 1,
        worker_id=worker_id,
        lease_generation=run.lease_generation,
        status="running",
        started_at=now,
    )
    session.add(attempt)
    await session.flush()
    return Assignment(
        run_id=run.id,
        operation_id=operation.id,
        attempt_id=attempt.id,
        attempt_no=attempt.attempt_no,
        lease_generation=run.lease_generation,
        lease_expires_at=run.lease_expires_at,
        deadline_at=deadline,
        server_time=now,
        config=config,
        operation=assigned_operation,
    )


async def lock_attempt(session: AsyncSession, attempt_id: UUID) -> tuple[Attempt, Operation, Run]:
    run = await session.scalar(
        select(Run)
        .join(Operation, Operation.run_id == Run.id)
        .join(Attempt, Attempt.operation_id == Operation.id)
        .where(Attempt.id == attempt_id)
        .with_for_update(of=Run)
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ControlError(409, "stale_attempt", "Attempt is unknown or no longer current")
    # Re-read after acquiring the lock: its wait may have outlived another report commit.
    attempt = await session.get(Attempt, attempt_id, populate_existing=True)
    operation = await session.get(Operation, attempt.operation_id, populate_existing=True)
    return attempt, operation, run


def check_lease(attempt: Attempt, operation: Operation, run: Run, body: HeartbeatRequest, now):
    deadline = _attempt_deadline(attempt.started_at, operation, run)
    if (
        attempt.worker_id != body.worker_id
        or attempt.lease_generation != body.lease_generation
        or attempt.status != "running"
        or run.status != "running"
        or run.lease_owner != body.worker_id
        or run.lease_generation != body.lease_generation
        or run.lease_expires_at <= now
        or deadline <= now
    ):
        raise ControlError(409, "stale_attempt", "Attempt lease expired or was superseded")
    return deadline


async def heartbeat(
    session: AsyncSession, attempt_id: UUID, body: HeartbeatRequest
) -> HeartbeatResponse:
    attempt, operation, run = await lock_attempt(session, attempt_id)
    now = await session.scalar(select(func.clock_timestamp()))
    deadline = check_lease(attempt, operation, run, body, now)
    run.last_heartbeat_at = now
    run.lease_expires_at = min(now + LEASE, deadline)
    await session.flush()
    return HeartbeatResponse(
        lease_expires_at=run.lease_expires_at, deadline_at=deadline, server_time=now
    )
