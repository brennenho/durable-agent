"""Control startup resources and frozen run configuration."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from durable_agent.contracts import AgentConfig, WorkspaceVersion
from durable_agent.control.agents import AgentRuntime
from durable_agent.control.autoscaling import run_worker_pool
from durable_agent.control.coordinator import advance_delegations
from durable_agent.control.errors import ControlError
from durable_agent.control.recovery import recover_expired
from durable_agent.crash_test import CrashTestHooks, HookInterrupted, open_hooks
from durable_agent.db.session import Database
from durable_agent.harness import ARGUMENTS
from durable_agent.sdk.loading import load_app

DATABASE_UNAVAILABLE = (InterfaceError, OperationalError, PoolTimeout)
logger = logging.getLogger(__name__)


class Settings(BaseModel):
    database_url: str
    tools_url: str
    model: str = Field(min_length=1)
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    agent_app: str | None = Field(default=None, min_length=1)
    agent_build_id: str | None = Field(default=None, min_length=1)
    worker_autoscale: bool = False
    worker_project: str = ""
    worker_max: int = Field(default=4, ge=2)

    @model_validator(mode="after")
    def validate_deployment(self):
        if (self.agent_app is None) != (self.agent_build_id is None):
            raise ValueError("AGENT_APP and AGENT_BUILD_ID must be configured together")
        if self.worker_autoscale and not self.worker_project:
            raise ValueError("WORKER_POOL_PROJECT is required when worker autoscaling is enabled")
        return self

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            tools_url=os.environ["TOOLS_URL"],
            model=os.environ["OPENAI_MODEL"],
            image_digest=os.environ["SANDBOX_IMAGE_DIGEST"],
            agent_app=os.environ.get("AGENT_APP"),
            agent_build_id=os.environ.get("AGENT_BUILD_ID"),
            worker_autoscale=os.environ.get("WORKER_AUTOSCALE", "false"),
            worker_project=os.environ.get("WORKER_POOL_PROJECT", ""),
            worker_max=os.environ.get("WORKER_MAX", "4"),
        )


@dataclass
class Runtime:
    db: Database
    tools: httpx.AsyncClient
    settings: Settings
    hooks: CrashTestHooks | None = None
    agents: AgentRuntime = field(default_factory=AgentRuntime)

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

    async def new_config(self, agent: str | None = None) -> AgentConfig:
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
        config = AgentConfig(
            model=self.settings.model,
            model_params={"max_output_tokens": 4096},
            tool_schemas=[
                {
                    "type": "function",
                    "name": name,
                    "parameters": arguments.model_json_schema(),
                    "strict": False,
                    **(
                        {
                            "description": "Delegate independent tasks to up to three subagents. "
                            "They start from your saved files and return reports; their file edits "
                            "do not change your workspace. Only one batch is allowed per run."
                        }
                        if name == "delegate"
                        else {}
                    ),
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
        return self.agents.freeze(config, agent) if agent is not None else config


async def recovery_loop(db: Database) -> None:
    while True:
        try:
            async with db.session() as session:
                await recover_expired(session)
                await session.commit()
        except DATABASE_UNAVAILABLE:
            logger.warning("Database unavailable during lease recovery", exc_info=True)
        await asyncio.sleep(1)


async def delegation_loop(
    db: Database, hooks: CrashTestHooks, agents: AgentRuntime | None = None
) -> None:
    while True:
        try:
            async with db.session() as session:
                events = await advance_delegations(session, agents=agents)
                await session.commit()
            for name, run_id, operation_id in events:
                try:
                    await hooks.hit(name, run_id=run_id, operation_id=operation_id)
                except HookInterrupted:
                    logger.info("Crash Test interrupted %s after commit", name)
        except DATABASE_UNAVAILABLE:
            logger.warning("Database unavailable during delegation recovery", exc_info=True)
        await asyncio.sleep(1)


@asynccontextmanager
async def open_runtime(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[Runtime]:
    agents = (
        AgentRuntime(
            await asyncio.to_thread(load_app, settings.agent_app),
            settings.agent_app,
            settings.agent_build_id,
        )
        if settings.agent_app is not None
        else AgentRuntime()
    )
    db = Database(settings.database_url)
    try:
        async with db.session() as session:
            await session.execute(text("SELECT 1"))
        async with (
            httpx.AsyncClient(
                base_url=settings.tools_url,
                timeout=10,
                transport=transport,
                limits=httpx.Limits(max_connections=10),
            ) as tools,
            open_hooks("control") as hooks,
        ):
            async with asyncio.TaskGroup() as tasks:
                recovery = tasks.create_task(recovery_loop(db))
                scaling = (
                    tasks.create_task(
                        run_worker_pool(db, settings.worker_project, settings.worker_max, hooks)
                    )
                    if settings.worker_autoscale
                    else None
                )
                delegation = tasks.create_task(delegation_loop(db, hooks, agents))
                try:
                    yield Runtime(db, tools, settings, hooks, agents)
                finally:
                    recovery.cancel()
                    if scaling:
                        scaling.cancel()
                    delegation.cancel()
    finally:
        await db.close()
