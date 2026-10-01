import asyncio
from contextlib import asynccontextmanager, nullcontext
from uuid import uuid4

import pytest
from durable_agent.db.models import WorkspaceVersion
from durable_agent.db.session import Database
from sqlalchemy import func, select, text


@asynccontextmanager
async def database(postgres_engine):
    db = Database(postgres_engine.url.render_as_string(hide_password=False))
    try:
        yield db
    finally:
        await db.close()


def test_writes_require_explicit_commit_and_reads_need_none(postgres_engine):
    async def exercise():
        async with database(postgres_engine) as db:
            version = WorkspaceVersion(id=uuid4(), archive=b"saved")
            async with db.session() as writer:
                writer.add(version)
                # Queries do not implicitly flush pending writes.
                assert await writer.scalar(select(func.count()).select_from(WorkspaceVersion)) == 0
                await writer.flush()
                async with db.session() as reader:
                    assert await reader.get(WorkspaceVersion, version.id) is None
                await writer.commit()
                assert version.archive == b"saved"  # No implicit reload after commit.
            async with db.session() as reader:
                assert (await reader.get(WorkspaceVersion, version.id)).archive == b"saved"

    asyncio.run(exercise())


@pytest.mark.parametrize("fail", [False, True], ids=["normal-exit", "exception"])
def test_uncommitted_writes_are_rolled_back_on_exit(postgres_engine, fail):
    async def exercise():
        async with database(postgres_engine) as db:
            version_id = uuid4()
            expected = pytest.raises(RuntimeError, match="interrupted") if fail else nullcontext()
            with expected:
                async with db.session() as session:
                    session.add(WorkspaceVersion(id=version_id, archive=b"unsaved"))
                    await session.flush()
                    if fail:
                        raise RuntimeError("interrupted before commit")
            async with db.session() as session:
                assert await session.get(WorkspaceVersion, version_id) is None

    asyncio.run(exercise())


def test_cancelled_task_rolls_back_and_propagates_cancellation(postgres_engine):
    async def exercise():
        async with database(postgres_engine) as db:
            version_id = uuid4()
            flushed = asyncio.Event()

            async def write():
                async with db.session() as session:
                    session.add(WorkspaceVersion(id=version_id, archive=b"unsaved"))
                    await session.flush()
                    flushed.set()
                    await asyncio.Future()

            async with asyncio.timeout(5):
                task = asyncio.create_task(write())
                try:
                    await flushed.wait()
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            async with db.session() as session:
                assert await session.get(WorkspaceVersion, version_id) is None
                session.add(WorkspaceVersion(id=version_id, archive=b"retry"))
                await session.commit()
            async with db.session() as session:
                assert (await session.get(WorkspaceVersion, version_id)).archive == b"retry"

    asyncio.run(exercise())


def test_database_reuses_connections_and_closes_the_pool(postgres_engine):
    async def exercise():
        async with database(postgres_engine) as db:
            async with db.session() as first:
                pid = await first.scalar(text("SELECT pg_backend_pid()"))
            async with db.session() as second:
                assert second is not first
                assert await second.scalar(text("SELECT pg_backend_pid()")) == pid
        return pid

    pid = asyncio.run(exercise())
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
            )
            == 0
        )
