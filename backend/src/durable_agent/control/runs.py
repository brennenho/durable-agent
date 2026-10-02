"""Run creation queries. The caller owns the session and commit."""

import json
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import AgentConfig, SubmitRunRequest
from durable_agent.control.agent_progress import start_agent
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.continuations import input_value, same_input
from durable_agent.control.errors import ControlError
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.harness import start


async def find_submission(session: AsyncSession, body: SubmitRunRequest) -> Run | None:
    row = (
        await session.execute(
            select(Run, Conversation.forked_from_operation_id)
            .join(Conversation, Run.conversation_id == Conversation.id)
            .where(Run.submission_key == body.submission_key)
        )
    ).one_or_none()
    if row is None:
        return None
    run, origin = row
    identity = run.config.get("agent")
    wrong_agent = identity is not None and identity["name"] != (body.agent or "builtin")
    if (
        wrong_agent
        or not same_input(run, body)
        or run.turn_index != 0
        or origin is not None
        or run.parent_operation_id is not None
    ):
        raise ControlError(409, "submission_conflict", "Submission key has different inputs")
    return run


async def create_run(
    session: AsyncSession,
    body: SubmitRunRequest,
    config: AgentConfig,
    *,
    agents: AgentRuntime | None = None,
) -> Run:
    if existing := await find_submission(session, body):
        return existing
    transition = None
    if config.agent is None:
        if body.is_sdk_input:
            raise ControlError(400, "unknown_agent", "SDK input requires a configured agent")
        transition = start(config, body.prompt)
        if transition.status == "failed":
            raise ControlError(400, transition.error.code, transition.error.message)
    value = input_value(body)
    prompt = (
        body.prompt
        if not body.is_sdk_input
        else value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False)
    )
    proposed_id = uuid4()
    session.add(Conversation(id=proposed_id))
    await session.flush()
    run_id = await session.scalar(
        insert(Run)
        .values(
            id=proposed_id,
            conversation_id=proposed_id,
            submission_key=body.submission_key,
            prompt=prompt,
            agent_input=value if config.agent is not None else None,
            config=config.model_dump(mode="json"),
            status="queued",
            next_attempt_at=func.clock_timestamp(),
        )
        .on_conflict_do_nothing(index_elements=[Run.submission_key])
        .returning(Run.id)
    )
    if run_id is None:
        # Remove this transaction's unused conversation after a competing submission wins.
        await session.execute(delete(Conversation).where(Conversation.id == proposed_id))
        return await find_submission(session, body)
    run = await session.get(Run, run_id)
    if config.agent is not None:
        now = await session.scalar(select(func.clock_timestamp()))
        await start_agent(session, run, agents, now)
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
