import asyncio
from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import pytest
from alembic import command
from durable_agent.db.models import Attempt, Operation, Run, ToolRequest, WorkspaceVersion
from durable_agent.db.session import create_database_engine, create_session_factory
from sqlalchemy import delete, func, insert, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def run_values(**overrides):
    return {
        "id": uuid4(),
        "submission_key": uuid4(),
        "prompt": "Edit a file",
        "config": {"model": "test-model"},
        "status": "queued",
        "next_attempt_at": NOW,
        **overrides,
    }


def add_operation(engine):
    run = run_values()
    operation_id = uuid4()
    with engine.begin() as connection:
        connection.execute(insert(Run).values(**run))
        connection.execute(
            insert(Operation).values(
                id=operation_id, run_id=run["id"], position=0, kind="model", request={}
            )
        )
    return run["id"], operation_id


def add_workspace(engine):
    version_id = uuid4()
    with engine.begin() as connection:
        connection.execute(insert(WorkspaceVersion).values(id=version_id, archive=b"seed archive"))
    return version_id


def test_migration_matches_models_and_round_trips(postgres_engine, migration_config):
    expected = {
        "runs",
        "operations",
        "attempts",
        "tool_requests",
        "workspace_versions",
        "alembic_version",
    }
    with postgres_engine.begin() as connection:
        migration_config.attributes["connection"] = connection
        assert set(inspect(connection).get_table_names()) == expected
        command.check(migration_config)
        command.downgrade(migration_config, "base")
        assert inspect(connection).get_table_names() == ["alembic_version"]
        command.upgrade(migration_config, "head")
        assert set(inspect(connection).get_table_names()) == expected
        command.check(migration_config)


def test_duplicate_submission_key_is_rejected(postgres_engine):
    key = uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(insert(Run).values(**run_values(submission_key=key)))
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(insert(Run).values(**run_values(submission_key=key)))
    assert caught.value.orig.diag.constraint_name == "uq_runs_submission_key"


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"status": "invalid", "next_attempt_at": None}, "ck_runs_status"),
        ({"lease_generation": -1}, "ck_runs_generation"),
        ({"lease_owner": uuid4()}, "ck_runs_lease"),
        ({"status": "running", "next_attempt_at": None, "lease_generation": 1}, "ck_runs_lease"),
        ({"next_attempt_at": None}, "ck_runs_claim_time"),
        ({"status": "succeeded", "next_attempt_at": None}, "ck_runs_finished"),
        (
            {"status": "failed", "next_attempt_at": None, "finished_at": NOW},
            "ck_runs_terminal_error",
        ),
    ],
)
def test_run_status_requires_consistent_fields(postgres_engine, changes, constraint):
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(insert(Run).values(**run_values(**changes)))
    assert caught.value.orig.diag.constraint_name == constraint


def test_run_can_claim_retry_and_finish(postgres_engine):
    values = run_values()
    with postgres_engine.begin() as connection:
        connection.execute(insert(Run).values(**values))
        for generation in (1, 2):
            connection.execute(
                update(Run)
                .where(Run.id == values["id"])
                .values(
                    status="running",
                    next_attempt_at=None,
                    lease_owner=uuid4(),
                    lease_generation=generation,
                    lease_expires_at=NOW + timedelta(seconds=30),
                    last_heartbeat_at=NOW,
                )
            )
            connection.execute(
                update(Run)
                .where(Run.id == values["id"])
                .values(
                    status="retrying", next_attempt_at=NOW, lease_owner=None, lease_expires_at=None
                )
            )
        connection.execute(
            update(Run)
            .where(Run.id == values["id"])
            .values(status="succeeded", next_attempt_at=None, finished_at=NOW)
        )
    with Session(postgres_engine) as session:
        run = session.get(Run, values["id"])
        assert run.status == "succeeded"
        assert run.lease_generation == 2
        assert run.last_heartbeat_at == NOW
        assert run.created_at.tzinfo is not None


def test_operation_order_is_unique_within_a_run(postgres_engine):
    run_id, _ = add_operation(postgres_engine)
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            insert(Operation).values(run_id=run_id, position=0, kind="tool", request={})
        )
    assert caught.value.orig.diag.constraint_name == "uq_operations_run_position"


@pytest.mark.parametrize("result,completed_at", [({"text": "Done"}, None), (None, NOW)])
def test_operation_result_and_completion_must_be_saved_together(
    postgres_engine, result, completed_at
):
    _, operation_id = add_operation(postgres_engine)
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            update(Operation)
            .where(Operation.id == operation_id)
            .values(result=result, completed_at=completed_at)
        )
    assert caught.value.orig.diag.constraint_name == "ck_operations_completion"


@pytest.mark.parametrize("existing,new", list(product(("running", "accepted"), repeat=2)))
def test_one_running_or_accepted_attempt_per_operation(postgres_engine, existing, new):
    _, operation_id = add_operation(postgres_engine)
    with postgres_engine.begin() as connection:
        connection.execute(
            insert(Attempt).values(
                operation_id=operation_id,
                attempt_no=1,
                worker_id=uuid4(),
                lease_generation=1,
                status=existing,
                finished_at=None if existing == "running" else NOW,
            )
        )
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            insert(Attempt).values(
                operation_id=operation_id,
                attempt_no=2,
                worker_id=uuid4(),
                lease_generation=2,
                status=new,
                finished_at=None if new == "running" else NOW,
            )
        )
    assert caught.value.orig.diag.constraint_name == "uq_attempts_one_active_or_accepted"


def test_retry_history_allows_a_new_attempt_but_never_reuses_its_number(postgres_engine):
    _, operation_id = add_operation(postgres_engine)
    with postgres_engine.begin() as connection:
        for number, status in enumerate(("error", "unknown", "running"), start=1):
            connection.execute(
                insert(Attempt).values(
                    operation_id=operation_id,
                    attempt_no=number,
                    worker_id=uuid4(),
                    lease_generation=number,
                    status=status,
                    finished_at=None if status == "running" else NOW,
                )
            )
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            insert(Attempt).values(
                operation_id=operation_id,
                attempt_no=1,
                worker_id=uuid4(),
                lease_generation=4,
                status="error",
                finished_at=NOW,
            )
        )
    assert caught.value.orig.diag.constraint_name == "uq_attempts_operation_number"


@pytest.mark.parametrize(
    "has_result,has_version,has_time",
    [bits for bits in product((False, True), repeat=3) if 0 < sum(bits) < 3],
)
def test_partial_tool_completion_is_rejected(postgres_engine, has_result, has_version, has_time):
    version_id = add_workspace(postgres_engine)
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            insert(ToolRequest).values(
                operation_key=uuid4(),
                request={},
                input_version_id=version_id,
                lease_generation=1,
                result={"status": "ok", "value": {}} if has_result else None,
                output_version_id=version_id if has_version else None,
                completed_at=NOW if has_time else None,
            )
        )
    assert caught.value.orig.diag.constraint_name == "ck_tool_requests_completion"


def test_tool_key_is_independent_of_runtime_and_read_can_reuse_input(postgres_engine):
    version_id = add_workspace(postgres_engine)
    operation_key = uuid4()
    with postgres_engine.begin() as connection:
        connection.execute(
            insert(ToolRequest).values(
                operation_key=operation_key,
                request={"name": "read_file"},
                input_version_id=version_id,
                lease_generation=1,
                result={"status": "ok", "value": {"content": "Saved"}},
                output_version_id=version_id,
                completed_at=NOW,
            )
        )
    with Session(postgres_engine) as session:
        assert session.get(Operation, operation_key) is None
        saved = session.get(ToolRequest, operation_key)
        assert saved.output_version_id == saved.input_version_id
        assert saved.result["value"]["content"] == "Saved"
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(delete(WorkspaceVersion).where(WorkspaceVersion.id == version_id))


def test_missing_referenced_rows_are_rejected(postgres_engine):
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            insert(Operation).values(run_id=uuid4(), position=0, kind="model", request={})
        )
    assert caught.value.orig.sqlstate == "23503"
    with pytest.raises(IntegrityError) as caught, postgres_engine.begin() as connection:
        connection.execute(
            insert(ToolRequest).values(
                operation_key=uuid4(), request={}, input_version_id=uuid4(), lease_generation=1
            )
        )
    assert caught.value.orig.sqlstate == "23503"


def test_session_transaction_publishes_checkpoint_and_result_together(postgres_engine):
    seed_id = add_workspace(postgres_engine)
    operation_key, output_id = uuid4(), uuid4()

    async def exercise():
        engine = create_database_engine(postgres_engine.url.render_as_string(hide_password=False))
        sessions = create_session_factory(engine)
        try:
            async with sessions.begin() as session:
                session.add(
                    ToolRequest(
                        operation_key=operation_key,
                        request={},
                        input_version_id=seed_id,
                        lease_generation=1,
                    )
                )
            with pytest.raises(RuntimeError, match="interrupted"):
                async with sessions.begin() as session:
                    session.add(WorkspaceVersion(id=output_id, archive=b"updated archive"))
                    await session.flush()
                    request = await session.get(ToolRequest, operation_key)
                    request.result = {"status": "ok", "value": {"bytes_written": 7}}
                    request.output_version_id = output_id
                    request.completed_at = NOW
                    await session.flush()
                    raise RuntimeError("interrupted before commit")
            async with sessions() as session:
                assert await session.get(WorkspaceVersion, output_id) is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ToolRequest)
                        .where(ToolRequest.result.is_(None))
                    )
                    == 1
                )
            async with sessions.begin() as session:
                session.add(WorkspaceVersion(id=output_id, archive=b"updated archive"))
                await session.flush()
                request = await session.get(ToolRequest, operation_key)
                request.result = {"status": "ok", "value": {"bytes_written": 7}}
                request.output_version_id = output_id
                request.completed_at = NOW
            async with sessions() as session:
                saved = await session.get(ToolRequest, operation_key)
                assert saved.output_version_id == output_id
                assert (
                    await session.get(WorkspaceVersion, output_id)
                ).archive == b"updated archive"
        finally:
            await engine.dispose()

    asyncio.run(exercise())
