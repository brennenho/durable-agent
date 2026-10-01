from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from durable_agent.contracts import AgentConfig
from durable_agent.db.session import Database


@pytest.fixture
def config():
    return AgentConfig(
        model="test",
        model_params={},
        tool_schemas=[{"name": "write_file"}, {"name": "read_file"}],
        initial_workspace_version_id=uuid4(),
        sandbox={"image_digest": "sha256:test", "workspace_max_bytes": 1024, "log_max_bytes": 1024},
        max_attempts=5,
        max_turns=20,
        call_timeout_seconds=120,
    )


@pytest.fixture
def database(postgres_engine):
    @asynccontextmanager
    async def opened():
        db = Database(postgres_engine.url.render_as_string(hide_password=False))
        try:
            yield db
        finally:
            await db.close()

    return opened
