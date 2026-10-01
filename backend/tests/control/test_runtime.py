import asyncio
from uuid import uuid4

import httpx
import pytest
from durable_agent.control.errors import ControlError
from durable_agent.control.runtime import Settings, open_runtime, recovery_loop
from durable_agent.control.views import detail
from durable_agent.db.session import Database
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from .test_leases import claimed, queued
from .test_recovery import expire


def settings(postgres_engine):
    return Settings(
        database_url=postgres_engine.url.render_as_string(hide_password=False),
        tools_url="http://tools",
        model="test",
        image_digest="sha256:" + "a" * 64,
    )


def test_startup_recovers_saved_work_and_builds_frozen_config(postgres_engine, database, config):
    async def exercise():
        seed = uuid4()
        async with database() as db:
            run_id = await queued(db, config)
            await claimed(db)
            await expire(db)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"workspace_version_id": str(seed)})
        )
        async with open_runtime(settings(postgres_engine), transport=transport) as runtime:
            frozen = await runtime.new_config()
            assert frozen.initial_workspace_version_id == seed
            assert frozen.sandbox.image_digest == "sha256:" + "a" * 64
            assert {schema["name"] for schema in frozen.tool_schemas} == {
                "read_file",
                "write_file",
                "list_files",
                "run_command",
            }
            async with asyncio.timeout(3):
                while True:
                    async with runtime.db.session() as session:
                        if (await detail(session, run_id)).status == "retrying":
                            break
                    await asyncio.sleep(0.01)
        assert runtime.tools.is_closed

    asyncio.run(exercise())


@pytest.mark.parametrize("failure_source", ["body", "recovery"])
def test_runtime_closes_resources_and_propagates_unexpected_failures(
    postgres_engine, monkeypatch, failure_source
):
    closed = []
    original_close = Database.close

    async def close(db):
        await original_close(db)
        closed.append(db)

    async def broken_recovery(session):
        raise RuntimeError("Recovery failed")

    monkeypatch.setattr(Database, "close", close)
    if failure_source == "recovery":
        monkeypatch.setattr("durable_agent.control.runtime.recover_expired", broken_recovery)

    async def exercise():
        with pytest.raises(ExceptionGroup) as caught:
            async with asyncio.timeout(3), open_runtime(settings(postgres_engine)) as runtime:
                if failure_source == "body":
                    raise RuntimeError("Request context failed")
                await asyncio.Event().wait()
        assert isinstance(caught.value.exceptions[0], RuntimeError)
        assert runtime.tools.is_closed
        assert closed == [runtime.db]

    asyncio.run(exercise())


@pytest.mark.parametrize("status,payload", [(503, {}), (404, {}), (200, {})])
def test_tool_failure_is_explicit(postgres_engine, status, payload):
    async def exercise():
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
        async with open_runtime(settings(postgres_engine), transport=transport) as runtime:
            with pytest.raises(ControlError) as caught:
                await runtime.new_config()
            assert caught.value.status == 503

    asyncio.run(exercise())


def test_startup_requires_pinned_image(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("TOOLS_URL", "http://tools")
    monkeypatch.setenv("OPENAI_MODEL", "test")
    monkeypatch.setenv("SANDBOX_IMAGE_DIGEST", "latest")
    with pytest.raises(ValidationError):
        Settings.from_environment()


def test_recovery_loop_retries_database_outage_and_propagates_cancellation(monkeypatch, database):
    calls = 0
    reached_retry = asyncio.Event()

    async def outage_then_recover(session):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("query", {}, OSError("unavailable"))
        reached_retry.set()
        return 0

    monkeypatch.setattr("durable_agent.control.runtime.recover_expired", outage_then_recover)

    async def exercise():
        async with database() as db:
            task = asyncio.create_task(recovery_loop(db))
            try:
                async with asyncio.timeout(3):
                    await reached_retry.wait()
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(exercise())
