"""SDK checkpoints extend the existing rows without rewriting legacy progress."""

from alembic import command
from durable_agent.db.models import Operation, Run
from sqlalchemy import select, update

from .test_database import NOW, add_operation


def test_migration_preserves_existing_run_and_operation(postgres_engine, migration_config):
    run_id, operation_id = add_operation(postgres_engine)
    with postgres_engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        command.downgrade(migration_config, "0005")
        command.upgrade(migration_config, "head")
        run = connection.execute(select(Run).where(Run.id == run_id)).one()
        operation = connection.execute(select(Operation).where(Operation.id == operation_id)).one()
        assert run.prompt == "Edit a file"
        assert run.status == "queued"
        assert (run.agent_input, run.agent_state, run.output) == (None, None, None)
        assert operation.request == {}
        assert operation.agent_state is None
        command.check(migration_config)


def test_historical_state_is_independent_of_current_state(postgres_engine):
    run_id, operation_id = add_operation(postgres_engine)
    with postgres_engine.begin() as connection:
        connection.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(
                agent_input={"document": "Draft"},
                agent_state={"phase": "review"},
            )
        )
        connection.execute(
            update(Operation)
            .where(Operation.id == operation_id)
            .values(
                agent_state={"phase": "review"},
                result={"text": "Draft"},
                completed_at=NOW,
            )
        )
    with postgres_engine.begin() as connection:
        connection.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(
                agent_state={"phase": "finished"},
                output=[{"approved": True}],
            )
        )
    with postgres_engine.connect() as connection:
        run = connection.execute(select(Run).where(Run.id == run_id)).one()
        checkpoint = connection.execute(select(Operation).where(Operation.id == operation_id)).one()
        assert run.agent_input == {"document": "Draft"}
        assert run.agent_state == {"phase": "finished"}
        assert run.output == [{"approved": True}]
        assert checkpoint.agent_state == {"phase": "review"}


def test_checkpoint_changes_roll_back_together(postgres_engine):
    run_id, operation_id = add_operation(postgres_engine)
    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            update(Run).where(Run.id == run_id).values(agent_state={"phase": "next"})
        )
        connection.execute(
            update(Operation)
            .where(Operation.id == operation_id)
            .values(
                agent_state={"phase": "next"},
                result={"saved": True},
                completed_at=NOW,
            )
        )
        transaction.rollback()
        run = connection.execute(select(Run).where(Run.id == run_id)).one()
        operation = connection.execute(select(Operation).where(Operation.id == operation_id)).one()
        assert run.agent_state is None
        assert operation.agent_state is None
        assert operation.result is None
        assert operation.completed_at is None
