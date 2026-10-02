"""Create a conversation turn from accepted progress; callers own the transaction."""

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import PreviousTurn, SubmitFollowUpRequest
from durable_agent.control.errors import ControlError
from durable_agent.control.views import load_operations, restore_state, workspace_version
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.harness import start


async def find_continuation(
    session: AsyncSession, conversation_id: UUID, body: SubmitFollowUpRequest
) -> Run | None:
    run = await session.scalar(select(Run).where(Run.submission_key == body.submission_key))
    if run is None:
        return None
    predecessor = await session.scalar(
        select(Run.id).where(
            Run.conversation_id == run.conversation_id, Run.turn_index == run.turn_index - 1
        )
    )
    if (
        run.conversation_id != conversation_id
        or run.prompt != body.prompt
        or predecessor != body.after_run_id
    ):
        raise ControlError(409, "submission_conflict", "Submission key has different inputs")
    return run


async def create_continuation(
    session: AsyncSession, conversation_id: UUID, body: SubmitFollowUpRequest
) -> Run:
    conversation = await session.scalar(
        select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
    )
    # Replays remain valid after this conversation has advanced to a later turn.
    if existing := await find_continuation(session, conversation_id, body):
        return existing
    if conversation is None:
        raise ControlError(404, "conversation_not_found", "Conversation not found")
    previous = await session.scalar(
        select(Run)
        .where(Run.conversation_id == conversation_id)
        .order_by(Run.turn_index.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if previous is None or previous.id != body.after_run_id:
        raise ControlError(409, "stale_conversation", "A newer conversation turn exists")
    if previous.parent_operation_id is not None:
        raise ControlError(
            409, "child_run_read_only", "Control this subagent through its parent run"
        )
    if previous.status not in ("succeeded", "failed"):
        raise ControlError(409, "conversation_busy", "The current turn is still running")

    state = restore_state(previous, await load_operations(session, previous.id))
    config = state.config.model_copy(
        update={"initial_workspace_version_id": workspace_version(state)}
    )
    previous_turns = [
        *state.previous_turns,
        PreviousTurn(
            prompt=previous.prompt, history=state.history, terminal_error=previous.terminal_error
        ),
    ]
    transition = start(config, body.prompt, previous_turns=previous_turns)
    if transition.status == "failed":
        raise ControlError(400, transition.error.code, transition.error.message)
    run_id = await session.scalar(
        insert(Run)
        .values(
            id=uuid4(),
            conversation_id=conversation_id,
            turn_index=previous.turn_index + 1,
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
        # A submission using the same key in another conversation may have won.
        existing = await find_continuation(session, conversation_id, body)
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
