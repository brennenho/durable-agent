from uuid import uuid4

import pytest
from alembic import command
from durable_agent.db.models import Conversation, Run
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError


def test_existing_runs_keep_ids_and_data_when_grouped(postgres_engine, migration_config):
    run_id, submission_key = uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        command.downgrade(migration_config, "0001")
        connection.execute(
            text(
                "INSERT INTO runs (id, submission_key, prompt, config, status, next_attempt_at) "
                "VALUES (:id, :key, 'Original prompt', '{\"model\":\"original\"}', 'queued', now())"
            ),
            {"id": run_id, "key": submission_key},
        )
        before = connection.execute(text("SELECT id, prompt, config, created_at FROM runs")).one()
        command.upgrade(migration_config, "head")
        after = connection.execute(text("SELECT id, prompt, config, created_at FROM runs")).one()
        assert before == after
        assert connection.execute(select(Run.conversation_id, Run.turn_index)).one() == (run_id, 0)
        assert connection.execute(select(Conversation.id, Conversation.created_at)).one() == (
            run_id,
            before.created_at,
        )


@pytest.mark.parametrize(
    "status,turn_index,constraint",
    [
        ("succeeded", 0, "uq_runs_conversation_turn"),
        ("queued", 1, "uq_runs_one_active_per_conversation"),
        ("queued", -1, "ck_runs_turn_index"),
    ],
)
def test_conversation_constraints(postgres_engine, status, turn_index, constraint):
    conversation_id = uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(insert(Conversation).values(id=conversation_id))
        connection.execute(
            insert(Run).values(
                conversation_id=conversation_id,
                submission_key=uuid4(),
                prompt="First",
                config={},
                status="queued",
                next_attempt_at=func.now(),
            )
        )
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            insert(Run).values(
                conversation_id=conversation_id,
                turn_index=turn_index,
                submission_key=uuid4(),
                prompt="Second",
                config={},
                status=status,
                next_attempt_at=func.now() if status == "queued" else None,
                finished_at=func.now() if status == "succeeded" else None,
            )
        )
    assert caught.value.orig.diag.constraint_name == constraint
