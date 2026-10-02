"""Create a conversation turn from accepted progress; callers own the transaction."""

import json
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import (
    AgentConfig,
    PreviousTurn,
    SubmitFollowUpRequest,
    SubmitRunRequest,
)
from durable_agent.control.agent_progress import start_agent
from durable_agent.control.agent_state import restore_context
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.errors import ControlError
from durable_agent.control.history import load_operations, restore_state, workspace_version
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.harness import start


def compatible_agent(run: Run, body: SubmitRunRequest) -> bool:
    name = (run.config.get("agent") or {}).get("name")
    if name is None:
        return not body.is_sdk_input
    return body.agent in (None, name) and (body.is_sdk_input or name == "builtin")


def input_value(body: SubmitRunRequest):
    return body.input if body.is_sdk_input else body.prompt


def same_input(run: Run, body: SubmitRunRequest) -> bool:
    if not compatible_agent(run, body):
        return False
    if run.config.get("agent") is not None:
        return json.dumps(run.agent_input, sort_keys=True) == json.dumps(
            input_value(body), sort_keys=True
        )
    return run.prompt == body.prompt


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
        or not same_input(run, body)
        or predecessor != body.after_run_id
    ):
        raise ControlError(409, "submission_conflict", "Submission key has different inputs")
    return run


async def create_continuation(
    session: AsyncSession,
    conversation_id: UUID,
    body: SubmitFollowUpRequest,
    *,
    agents: AgentRuntime | None = None,
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

    if not compatible_agent(previous, body):
        raise ControlError(
            409, "agent_mismatch", "Agent or input format differs from the source run"
        )
    operations = await load_operations(session, previous.id)
    agent_state = None
    if previous.config.get("agent") is not None:
        context = restore_context(previous, operations)
        config = AgentConfig.model_validate(previous.config).model_copy(
            update={
                "initial_workspace_version_id": context.workspace_version_id,
                "agent_history": list(context.history),
            }
        )
        agent_state = context.state
    else:
        state = restore_state(previous, operations)
        config = state.config.model_copy(
            update={"initial_workspace_version_id": workspace_version(state)}
        )
        previous_turns = [
            *state.previous_turns,
            PreviousTurn(
                prompt=previous.prompt,
                history=state.history,
                terminal_error=previous.terminal_error,
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
            agent_input=input_value(body) if config.agent is not None else None,
            agent_state=agent_state,
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
    run = await session.get(Run, run_id)
    if config.agent is not None:
        await start_agent(
            session, run, agents, await session.scalar(select(func.clock_timestamp()))
        )
    else:
        session.add(
            Operation(
                run_id=run_id,
                position=0,
                kind="model",
                request=transition.next.request.model_dump(mode="json"),
            )
        )
    await session.flush()
    return run
