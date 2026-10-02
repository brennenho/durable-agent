"""Branch from an accepted closed round; callers commit the new conversation atomically."""

from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import PreviousTurn, SubmitForkRequest
from durable_agent.control.errors import ControlError
from durable_agent.control.history import branch_checkpoints, load_operations, restore_state
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.harness import start


async def find_fork(session: AsyncSession, source_id: UUID, body: SubmitForkRequest) -> Run | None:
    row = (
        await session.execute(
            select(Run, Conversation.forked_from_operation_id, Operation.run_id)
            .select_from(Run)
            .join(Conversation, Run.conversation_id == Conversation.id)
            .outerjoin(Operation, Operation.id == Conversation.forked_from_operation_id)
            .where(Run.submission_key == body.submission_key)
        )
    ).one_or_none()
    if row is None:
        return None
    run, origin, origin_run = row
    if (
        run.turn_index != 0
        or run.prompt != body.prompt
        or origin != body.after_operation_id
        or origin_run != source_id
    ):
        raise ControlError(409, "submission_conflict", "Submission key has different inputs")
    return run


async def create_fork(session: AsyncSession, source_id: UUID, body: SubmitForkRequest) -> Run:
    if existing := await find_fork(session, source_id, body):
        return existing
    source = await session.scalar(
        select(Run)
        .where(Run.id == source_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise ControlError(404, "run_not_found", "Run not found")
    if source.parent_operation_id is not None:
        raise ControlError(
            409, "child_run_read_only", "Control this subagent through its parent run"
        )
    operations = await load_operations(session, source_id)
    selected = next((op for op in operations if op.id == body.after_operation_id), None)
    if selected is None:
        raise ControlError(404, "operation_not_found", "Operation not found in this run")
    prefix = [op for op in operations if op.position <= selected.position]
    checkpoint = branch_checkpoints(source, prefix).get(selected.id)
    if checkpoint is None:
        raise ControlError(
            409, "checkpoint_not_available", "This operation is not a completed checkpoint"
        )
    state = restore_state(source, prefix)
    config = state.config.model_copy(
        update={"initial_workspace_version_id": checkpoint.workspace_version_id}
    )
    transition = start(
        config,
        body.prompt,
        previous_turns=[
            *state.previous_turns,
            PreviousTurn(prompt=source.prompt, history=state.history),
        ],
    )
    if transition.status == "failed":
        raise ControlError(400, transition.error.code, transition.error.message)
    proposed_id = uuid4()
    session.add(Conversation(id=proposed_id, forked_from_operation_id=selected.id))
    await session.flush()
    run_id = await session.scalar(
        insert(Run)
        .values(
            id=proposed_id,
            conversation_id=proposed_id,
            submission_key=body.submission_key,
            prompt=body.prompt,
            config=config.model_dump(mode="json"),
            status="queued",
            next_attempt_at=func.clock_timestamp(),
        )
        .on_conflict_do_nothing(index_elements=[Run.submission_key])
        .returning(Run.id)
    )
    if run_id is None:
        await session.execute(delete(Conversation).where(Conversation.id == proposed_id))
        existing = await find_fork(session, source_id, body)
        if existing is None:
            raise RuntimeError("Conflicting submission disappeared")
        return existing
    session.add(
        Operation(
            run_id=run_id,
            position=0,
            kind="model",
            request=transition.next.request.model_dump(mode="json"),
        )
    )
    await session.flush()
    return await session.get(Run, run_id)
