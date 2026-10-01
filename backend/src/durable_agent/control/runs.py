"""Run creation queries. The caller owns the session and commit."""

from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from durable_agent.contracts import AgentConfig, SubmitRunRequest
from durable_agent.control.errors import ControlError
from durable_agent.db.models import Conversation, Operation, Run
from durable_agent.harness import start


async def find_submission(session: AsyncSession, body: SubmitRunRequest) -> Run | None:
    run = await session.scalar(select(Run).where(Run.submission_key == body.submission_key))
    if run is not None and run.prompt != body.prompt:
        raise ControlError(409, "submission_conflict", "Submission key has a different prompt")
    return run


async def create_run(session: AsyncSession, body: SubmitRunRequest, config: AgentConfig) -> Run:
    if existing := await find_submission(session, body):
        return existing
    transition = start(config, body.prompt)
    if transition.status == "failed":
        raise ControlError(400, transition.error.code, transition.error.message)
    proposed_id = uuid4()
    session.add(Conversation(id=proposed_id))
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
        # Remove this transaction's unused conversation after a competing submission wins.
        await session.execute(delete(Conversation).where(Conversation.id == proposed_id))
        return await find_submission(session, body)
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
