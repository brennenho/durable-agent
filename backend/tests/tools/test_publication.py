import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from durable_agent.contracts import ToolExecuteRequest, ToolSuccess
from durable_agent.db.models import ToolRequest, WorkspaceVersion
from durable_agent.db.session import Database
from durable_agent.tools.publication import (
    ToolConflict,
    ToolDeadlineExceeded,
    publish_tool,
    register_tool,
)
from durable_agent.tools.workspaces import capture_initial_workspace, save_workspace
from sqlalchemy import func, select


@asynccontextmanager
async def registered(postgres_engine):
    db = Database(postgres_engine.url.render_as_string(hide_password=False))
    try:
        async with db.session() as session:
            seed = await save_workspace(session, capture_initial_workspace(None, 0))
            request = ToolExecuteRequest(
                operation_id=uuid4(),
                input_version_id=seed,
                name="write_file",
                arguments={"path": "a.md", "content": "saved"},
                lease_generation=1,
                deadline_at=datetime.now(UTC) + timedelta(minutes=1),
                sandbox={
                    "image_digest": "sha256:test",
                    "workspace_max_bytes": 1024,
                    "log_max_bytes": 1024,
                },
            )
            assert await register_tool(session, request) is None
            await session.commit()
        yield db, request
    finally:
        await db.close()


def test_overlapping_publications_and_lost_ack_reuse_one_result(postgres_engine):
    async def exercise():
        async with registered(postgres_engine) as (db, request):

            async def publish(value):
                async with db.session() as session:
                    result = await publish_tool(
                        session,
                        request,
                        ToolSuccess(status="ok", value={"winner": value}),
                        capture_initial_workspace(None, 0),
                    )
                    await session.commit()
                    return result

            first, second = await asyncio.gather(publish(1), publish(2))
            assert first == second
            async with db.session() as session:
                assert await register_tool(session, request) == first
                assert await session.scalar(select(func.count()).select_from(WorkspaceVersion)) == 2
            changed = request.model_copy(update={"arguments": {"path": "other"}})
            async with db.session() as session:
                with pytest.raises(ToolConflict, match="different input"):
                    await register_tool(session, changed)

    asyncio.run(exercise())


def test_rollback_discards_result_and_checkpoint_and_new_generation_fences_old(postgres_engine):
    async def exercise():
        async with registered(postgres_engine) as (db, request):
            with pytest.raises(RuntimeError, match="crash"):
                async with db.session() as session:
                    await publish_tool(
                        session,
                        request,
                        ToolSuccess(status="ok", value={}),
                        capture_initial_workspace(None, 0),
                    )
                    raise RuntimeError("crash before commit")
            async with db.session() as session:
                assert (await session.get(ToolRequest, request.operation_id)).completed_at is None
                assert await session.scalar(select(func.count()).select_from(WorkspaceVersion)) == 1
            newer = request.model_copy(update={"lease_generation": 2})
            async with db.session() as session:
                assert await register_tool(session, newer) is None
                await session.commit()
            async with db.session() as session:
                with pytest.raises(ToolConflict, match="superseded"):
                    await publish_tool(
                        session,
                        request,
                        ToolSuccess(status="ok", value={}),
                        capture_initial_workspace(None, 0),
                    )
            async with db.session() as session:
                result = await publish_tool(
                    session, newer, ToolSuccess(status="ok", value={}), None
                )
                await session.commit()
                assert result.workspace_version_id == request.input_version_id

    asyncio.run(exercise())


def test_concurrent_registration_keeps_highest_generation(postgres_engine):
    async def exercise():
        async with registered(postgres_engine) as (db, request):

            async def register(generation):
                async with db.session() as session:
                    await register_tool(
                        session, request.model_copy(update={"lease_generation": generation})
                    )
                    await session.commit()

            results = await asyncio.gather(register(2), register(3), return_exceptions=True)
            assert all(value is None or isinstance(value, ToolConflict) for value in results)
            async with db.session() as session:
                assert (await session.get(ToolRequest, request.operation_id)).lease_generation == 3

    asyncio.run(exercise())


def test_expired_or_unregistered_attempt_cannot_publish(postgres_engine):
    async def exercise():
        async with registered(postgres_engine) as (db, request):
            expired = request.model_copy(
                update={"deadline_at": datetime.now(UTC) - timedelta(seconds=1)}
            )
            async with db.session() as session:
                with pytest.raises(ToolDeadlineExceeded):
                    await publish_tool(
                        session,
                        expired,
                        ToolSuccess(status="ok", value={}),
                        capture_initial_workspace(None, 0),
                    )
            async with db.session() as session:
                with pytest.raises(ToolConflict, match="not registered"):
                    await publish_tool(
                        session,
                        request.model_copy(update={"lease_generation": 2}),
                        ToolSuccess(status="ok", value={}),
                        None,
                    )

    asyncio.run(exercise())
