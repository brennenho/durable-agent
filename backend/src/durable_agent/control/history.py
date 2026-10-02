"""Reconstruct accepted run history; callers own locks and transactions."""

from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import (
    AgentConfig,
    BranchCheckpoint,
    ModelRequest,
    ModelResult,
    OperationResult,
)
from durable_agent.db.models import Operation, Run
from durable_agent.harness.checkpoints import closed_rounds
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


def branch_checkpoints(run: Run, operations: list[Operation]) -> dict[UUID, BranchCheckpoint]:
    accepted = []
    for operation in operations:
        if operation.completed_at is None:
            break
        accepted.append(operation)
    config = AgentConfig.model_validate(run.config)
    if config.agent is not None and config.agent.name != "builtin":
        version = config.initial_workspace_version_id
        checkpoints = {}
        for operation in accepted:
            if operation.kind == "tool":
                version = UUID(operation.result["workspace_version_id"])
            checkpoints[operation.id] = BranchCheckpoint(workspace_version_id=version)
        return checkpoints
    rounds = closed_rounds(restore_state(run, accepted))
    return {
        accepted[index].id: BranchCheckpoint(workspace_version_id=version)
        for index, version in rounds.items()
    }


async def load_operations(session: AsyncSession, run_id: UUID) -> list[Operation]:
    return list(
        await session.scalars(
            select(Operation)
            .where(Operation.run_id == run_id)
            .order_by(Operation.position)
            .execution_options(populate_existing=True)
        )
    )
