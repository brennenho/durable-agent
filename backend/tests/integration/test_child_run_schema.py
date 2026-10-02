from uuid import uuid4

import pytest
from durable_agent.contracts import DelegateArguments
from durable_agent.db.models import Run
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from .test_database import NOW, add_operation, insert_run, run_values


def test_child_identity_is_unique_and_parent_must_exist(postgres_engine):
    _, operation_id = add_operation(postgres_engine)
    with postgres_engine.begin() as connection:
        for index in range(3):
            insert_run(connection, run_values(parent_operation_id=operation_id, child_index=index))
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        insert_run(connection, run_values(parent_operation_id=operation_id, child_index=0))
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        insert_run(connection, run_values(parent_operation_id=uuid4(), child_index=0))


@pytest.mark.parametrize("parent,index", [(False, 0), (True, None), (True, -1), (True, 3)])
def test_child_identity_requires_both_fields_and_bounded_index(postgres_engine, parent, index):
    _, operation_id = add_operation(postgres_engine)
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        insert_run(
            connection,
            run_values(parent_operation_id=operation_id if parent else None, child_index=index),
        )


def test_waiting_parent_remains_active_without_a_claim_or_lease(postgres_engine):
    values = run_values(status="waiting", next_attempt_at=None)
    with postgres_engine.begin() as connection:
        insert_run(connection, values)
        row = connection.execute(select(Run).where(Run.id == values["id"])).one()
        assert row.parent_operation_id is None
        assert row.lease_owner is None
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(
            insert(Run).values(
                **run_values(conversation_id=values["conversation_id"], turn_index=1)
            )
        )
    for overrides in ({"next_attempt_at": NOW}, {"lease_owner": uuid4()}):
        with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
            insert_run(connection, run_values(status="waiting", next_attempt_at=None) | overrides)


@pytest.mark.parametrize(
    "tasks",
    [
        [],
        [{"name": "a", "prompt": "b"}] * 4,
        [{"name": " ", "prompt": "b"}],
        [{"name": "a", "prompt": "b" * 8001}],
    ],
)
def test_delegation_is_bounded(tasks):
    with pytest.raises(ValidationError):
        DelegateArguments(tasks=tasks)


def test_delegation_preserves_task_order():
    args = DelegateArguments(
        tasks=[{"name": " Write ", "prompt": "first"}, {"name": "Review", "prompt": "second"}]
    )
    assert [task.name for task in args.tasks] == ["Write", "Review"]
