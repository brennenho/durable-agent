"""Control startup resources and frozen run configuration."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from durable_agent.contracts import AgentConfig, WorkspaceVersion
from durable_agent.control.errors import ControlError
from durable_agent.control.recovery import recover_expired
from durable_agent.db.session import Database
from durable_agent.harness import ARGUMENTS

DATABASE_UNAVAILABLE = (InterfaceError, OperationalError, PoolTimeout)
logger = logging.getLogger(__name__)


class Settings(BaseModel):
    database_url: str
    tools_url: str
    model: str = Field(min_length=1)
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            tools_url=os.environ["TOOLS_URL"],
            model=os.environ["OPENAI_MODEL"],
            image_digest=os.environ["SANDBOX_IMAGE_DIGEST"],
        )


@dataclass
class Runtime:
    db: Database
    tools: httpx.AsyncClient
    settings: Settings

    async def tools_get(self, path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            response = await self.tools.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ControlError(
                503, "tools_unavailable", "Tool service unavailable", retryable=True
            ) from exc
        if response.status_code == 404 and path != "/tools/workspace/initial":
            raise ControlError(404, "file_not_found", "Saved workspace or file not found")
        if not response.is_success:
            raise ControlError(503, "tools_unavailable", "Tool service unavailable", retryable=True)
        return response

    async def new_config(self) -> AgentConfig:
        response = await self.tools_get("/tools/workspace/initial")
        try:
            seed = WorkspaceVersion.model_validate_json(response.content).workspace_version_id
        except ValidationError as exc:
            raise ControlError(
                503,
                "invalid_tool_response",
                "Tool service returned an invalid workspace",
                retryable=True,
            ) from exc
        return AgentConfig(
            model=self.settings.model,
            model_params={"max_output_tokens": 4096},
            tool_schemas=[
                {
                    "type": "function",
                    "name": name,
                    "parameters": arguments.model_json_schema(),
                    "strict": False,
                }
                for name, arguments in ARGUMENTS.items()
            ],
            initial_workspace_version_id=seed,
            sandbox={
                "image_digest": self.settings.image_digest,
                "workspace_max_bytes": 20 * 1024 * 1024,
                "log_max_bytes": 64 * 1024,
            },
            max_attempts=5,
            max_turns=20,
            call_timeout_seconds=120,
        )


async def recovery_loop(db: Database) -> None:
    while True:
        try:
            async with db.session() as session:
                await recover_expired(session)
                await session.commit()
        except DATABASE_UNAVAILABLE:
            logger.warning("Database unavailable during lease recovery", exc_info=True)
        await asyncio.sleep(1)


@asynccontextmanager
async def open_runtime(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[Runtime]:
    db = Database(settings.database_url)
    try:
        async with db.session() as session:
            await session.execute(text("SELECT 1"))
        async with httpx.AsyncClient(
            base_url=settings.tools_url,
            timeout=10,
            transport=transport,
            limits=httpx.Limits(max_connections=10),
        ) as tools:
            async with asyncio.TaskGroup() as tasks:
                recovery = tasks.create_task(recovery_loop(db))
                try:
                    yield Runtime(db, tools, settings)
                finally:
                    recovery.cancel()
    finally:
        await db.close()
