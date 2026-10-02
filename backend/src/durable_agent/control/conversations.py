"""Conversation views derived from ordered runs; no separate cached chat state."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from durable_agent.contracts import ConversationDetail, ConversationSummary, ForkOrigin
from durable_agent.control.errors import ControlError
from durable_agent.control.views import detail
from durable_agent.db.models import Conversation, Operation, Run


def title(prompt: str, agent_name: str | None = None) -> str:
    fallback = agent_name if agent_name and agent_name != "builtin" else "New chat"
    return " ".join(prompt.split())[:80] or fallback


async def list_conversations(session: AsyncSession) -> list[ConversationSummary]:
    latest = aliased(
        Run,
        select(Run)
        .ext(distinct_on(Run.conversation_id))
        .order_by(Run.conversation_id, Run.turn_index.desc())
        .subquery(),
    )
    first = aliased(Run)
    progress = (
        select(Operation.run_id, func.max(Operation.completed_at).label("at"))
        .group_by(Operation.run_id)
        .subquery()
    )
    updated = func.greatest(latest.created_at, latest.finished_at, progress.c.at)
    rows = await session.execute(
        select(
            Conversation.id,
            Conversation.created_at,
            first.prompt,
            first.config["agent"]["name"].as_string().label("agent_name"),
            latest.id.label("run_id"),
            latest.status,
            latest.pause_requested,
            updated.label("updated_at"),
        )
        .join(latest, latest.conversation_id == Conversation.id)
        .join(first, (first.conversation_id == Conversation.id) & (first.turn_index == 0))
        .outerjoin(progress, progress.c.run_id == latest.id)
        .where(latest.parent_operation_id.is_(None))
        .order_by(updated.desc(), Conversation.id.desc())
    )
    return [
        ConversationSummary(
            id=row.id,
            title=title(row.prompt, row.agent_name),
            created_at=row.created_at,
            updated_at=row.updated_at,
            status=row.status,
            pause_requested=row.pause_requested,
            latest_run_id=row.run_id,
        )
        for row in rows
    ]


async def conversation_detail(session: AsyncSession, conversation_id: UUID) -> ConversationDetail:
    # Submissions lock this row; hold membership stable while reading individual turns.
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update(read=True)
    )
    if conversation is None:
        raise ControlError(404, "conversation_not_found", "Conversation not found")
    run_ids = list(
        await session.scalars(
            select(Run.id).where(Run.conversation_id == conversation_id).order_by(Run.turn_index)
        )
    )
    runs = [await detail(session, run_id) for run_id in run_ids]
    latest = runs[-1]
    origin = None
    if conversation.forked_from_operation_id is not None:
        row = (
            await session.execute(
                select(
                    Run.conversation_id,
                    Operation.run_id,
                    Operation.id.label("operation_id"),
                    Operation.position,
                )
                .join(Run, Run.id == Operation.run_id)
                .where(Operation.id == conversation.forked_from_operation_id)
            )
        ).one()
        origin = ForkOrigin(**row._mapping)
    return ConversationDetail(
        id=conversation.id,
        title=title(runs[0].prompt, runs[0].agent.name if runs[0].agent else None),
        created_at=conversation.created_at,
        updated_at=max(
            value
            for value in (latest.created_at, latest.finished_at, latest.last_progress_at)
            if value is not None
        ),
        status=latest.status,
        pause_requested=latest.pause_requested,
        latest_run_id=latest.id,
        runs=runs,
        forked_from=origin,
    )
