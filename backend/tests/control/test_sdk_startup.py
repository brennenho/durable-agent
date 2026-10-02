import asyncio
from uuid import uuid4

import httpx
import pytest
from durable_agent.control.runtime import Settings, open_runtime
from durable_agent.sdk.loading import AgentApp
from pydantic import ValidationError

from .test_agent_progress import Counter
from .test_runtime import settings

app = AgentApp(agents=[Counter])


@pytest.mark.parametrize("overrides", [{"agent_app": "app:app"}, {"agent_build_id": "build"}])
def test_custom_app_requires_an_explicit_build_identity(overrides):
    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            database_url="postgresql://localhost/test",
            tools_url="http://tools",
            model="test",
            image_digest="sha256:" + "a" * 64,
            **overrides,
        )


def test_startup_loads_the_app_once_and_freezes_selected_agent(postgres_engine):
    async def exercise():
        configured = settings(postgres_engine).model_copy(
            update={"agent_app": f"{__name__}:app", "agent_build_id": "build-1"}
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"workspace_version_id": str(uuid4())})
        )
        async with open_runtime(configured, transport=transport) as runtime:
            assert runtime.agents.app is app
            selected = await runtime.new_config("counter")
            assert selected.agent.name == "counter"
            assert selected.agent.app_ref == f"{__name__}:app"
            assert selected.agent.build_id == "build-1"
            assert isinstance(runtime.agents.instance(selected), Counter)
            builtin = await runtime.new_config("builtin")
            assert builtin.agent.name == "builtin"

    asyncio.run(exercise())


def test_invalid_app_reference_fails_startup(postgres_engine):
    async def exercise():
        configured = settings(postgres_engine).model_copy(
            update={"agent_app": f"{__name__}:missing", "agent_build_id": "build-1"}
        )
        with pytest.raises(ValueError, match="missing"):
            async with open_runtime(configured):
                pytest.fail("Invalid app must fail startup")

    asyncio.run(exercise())
