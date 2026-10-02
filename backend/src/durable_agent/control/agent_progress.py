"""Persist a callback decision in the transaction that accepts its triggering result."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import AgentConfig, ExecutionError, OperationResult
from durable_agent.control.agent_state import restore_context
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.control.history import load_operations
from durable_agent.db.models import Operation, Run
from durable_agent.harness.sdk_driver import invoke, plan_step
from durable_agent.harness.sdk_results import to_step_result
from durable_agent.sdk import Decision, Done, Fail, Next


def _callback(agents, run, context, result=None) -> Decision:
    try:
        agent = (agents or AgentRuntime()).instance(AgentConfig.model_validate(run.config))
        return invoke(agent.start if result is None else agent.advance, context, result)
    except ControlError as exc:
        return Fail(code=exc.error.code, message=exc.error.message)
    except Exception as exc:
        return Fail(code="agent_callback_failed", message=f"{type(exc).__name__}: {exc}")


def _apply(session, run, decision, now, operations, workspace_version_id):
    if isinstance(decision, Next):
        operation_id = uuid4()
        try:
            planned = plan_step(
                decision.step,
                AgentConfig.model_validate(run.config),
                operation_id,
                workspace_version_id,
                steps_taken=len(operations),
                can_delegate=run.parent_operation_id is None
                and not any(
                    op.kind == "tool" and op.request["call"]["name"] == "delegate"
                    for op in operations
                ),
            )
        except ValueError as exc:
            decision = Fail(code="invalid_agent_step", message=str(exc))
        else:
            run.agent_state = decision.state
            session.add(
                Operation(
                    id=operation_id,
                    run_id=run.id,
                    position=len(operations),
                    kind=planned.kind,
                    request=planned.request.model_dump(mode="json"),
                )
            )
            waiting = planned.kind == "tool" and planned.request.call.name == "delegate"
            run.status, run.next_attempt_at = ("waiting", None) if waiting else ("queued", now)
            return
    run.status = "succeeded" if isinstance(decision, Done) else "failed"
    run.finished_at, run.next_attempt_at, run.pause_requested = now, None, False
    if isinstance(decision, Done):
        run.output = decision.output
        if "state" in decision.model_fields_set:
            run.agent_state = decision.state
        run.terminal_error = None
    else:
        run.terminal_error = ExecutionError(
            code=decision.code, message=decision.message, retryable=False
        ).model_dump(mode="json")


async def start_agent(
    session: AsyncSession, run: Run, agents: AgentRuntime | None, now: datetime
) -> None:
    """Initialize an added run; the caller supplies frozen config and inherited state."""
    context = restore_context(run, [])
    _apply(session, run, _callback(agents, run, context), now, [], context.workspace_version_id)


async def advance_agent(
    session: AsyncSession,
    run: Run,
    operation: Operation,
    result: OperationResult,
    now: datetime,
    agents: AgentRuntime | None,
) -> None:
    """Caller holds the run lock, validates the result, and commits the whole transition."""
    if operation.completed_at is not None:
        raise ControlError(409, "operation_complete", "Operation already has an accepted result")
    operations = await load_operations(session, run.id)
    context = restore_context(run, operations)
    accepted = to_step_result(
        operation.id,
        result,
        context.workspace_version_id,
        tool_name=operation.request["call"]["name"] if operation.kind == "tool" else None,
    )
    decision = _callback(agents, run, context, accepted)
    operation.result, operation.completed_at = result.model_dump(mode="json"), now
    run.lease_owner = run.lease_expires_at = None
    _apply(session, run, decision, now, operations, accepted.workspace_version_id)
    operation.agent_state = run.agent_state
