import asyncio
from contextlib import asynccontextmanager, nullcontext
from uuid import uuid4

import pytest
from durable_agent.db.models import WorkspaceVersion
from durable_agent.db.session import create_database_engine, create_session_factory
from sqlalchemy import func, select


@asynccontextmanager
async def session_factory(postgres_engine):
    engine = create_database_engine(postgres_engine.url.render_as_string(hide_password=False))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


def test_writes_require_explicit_commit_and_reads_need_none(postgres_engine):
    async def exercise():
        async with session_factory(postgres_engine) as db_session:
            version = WorkspaceVersion(id=uuid4(), archive=b"saved")
            async with db_session() as writer:
                writer.add(version)
                # Queries do not implicitly flush pending writes.
                assert await writer.scalar(select(func.count()).select_from(WorkspaceVersion)) == 0
                await writer.flush()
                async with db_session() as reader:
                    assert await reader.get(WorkspaceVersion, version.id) is None
                await writer.commit()
                assert version.archive == b"saved"  # No implicit reload after commit.
            async with db_session() as reader:
                assert (await reader.get(WorkspaceVersion, version.id)).archive == b"saved"

    asyncio.run(exercise())


@pytest.mark.parametrize("fail", [False, True], ids=["normal-exit", "exception"])
def test_uncommitted_writes_are_rolled_back_on_exit(postgres_engine, fail):
    async def exercise():
        async with session_factory(postgres_engine) as db_session:
            version_id = uuid4()
            expected = pytest.raises(RuntimeError, match="interrupted") if fail else nullcontext()
            with expected:
                async with db_session() as session:
                    session.add(WorkspaceVersion(id=version_id, archive=b"unsaved"))
                    await session.flush()
                    if fail:
                        raise RuntimeError("interrupted before commit")
            async with db_session() as session:
                assert await session.get(WorkspaceVersion, version_id) is None

    asyncio.run(exercise())


def test_cancelled_task_rolls_back_and_propagates_cancellation(postgres_engine):
    async def exercise():
        async with session_factory(postgres_engine) as db_session:
            version_id = uuid4()
            flushed = asyncio.Event()

            async def write():
                async with db_session() as session:
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
            async with db_session() as session:
                assert await session.get(WorkspaceVersion, version_id) is None
                session.add(WorkspaceVersion(id=version_id, archive=b"retry"))
                await session.commit()
            async with db_session() as session:
                assert (await session.get(WorkspaceVersion, version_id)).archive == b"retry"

    asyncio.run(exercise())
