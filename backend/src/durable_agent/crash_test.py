"""Opt-in execution boundaries for a separate Crash Test controller."""

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)
Service = Literal["control", "tools", "worker"]


class HookEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID = Field(default_factory=uuid4)
    name: str
    service: Service
    instance: str
    pid: int
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: UUID | None = None
    operation_id: UUID | None = None
    attempt_id: UUID | None = None
    worker_id: UUID | None = None
    lease_generation: int | None = None
    sandbox_id: str | None = None
    workspace_version_id: UUID | None = None


class HookReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["continue", "interrupt"]


class HookInterrupted(RuntimeError):
    """The controller explicitly interrupted this action; normal recovery still applies."""


class CrashTestHooks:
    def __init__(
        self,
        service: Service,
        client: httpx.AsyncClient | None = None,
        *,
        endpoint: str = "/hooks",
        timeout_seconds: float = 10,
    ):
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Crash Test hook timeout must be between 0 and 30 seconds")
        self.service = service
        self.client = client
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.instance = socket.gethostname()
        self.pid = os.getpid()

    async def hit(self, name: str, **context) -> None:
        if self.client is None:
            return
        event = HookEvent(
            name=name, service=self.service, instance=self.instance, pid=self.pid, **context
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.post(
                    self.endpoint, json=event.model_dump(mode="json", exclude_none=True)
                )
                response.raise_for_status()
                reply = HookReply.model_validate_json(response.content)
        except (httpx.HTTPError, TimeoutError, ValidationError) as exc:
            # A missing observer must not invent a fault; its test is inconclusive.
            logger.warning(
                "Crash Test hook %s was not acknowledged (%s); continuing", name, type(exc).__name__
            )
            return
        if reply.action == "interrupt":
            raise HookInterrupted(f"Crash Test interrupted {name}")


@asynccontextmanager
async def open_hooks(service: Service):
    endpoint = os.environ.get("CRASH_TEST_HOOK_URL")
    if not endpoint:
        yield CrashTestHooks(service)
        return
    url = httpx.URL(endpoint)
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise ValueError("CRASH_TEST_HOOK_URL must be an absolute HTTP(S) URL without credentials")
    timeout = float(os.environ.get("CRASH_TEST_HOOK_TIMEOUT_SECONDS", "10"))
    headers = {}
    if token := os.environ.get("CRASH_TEST_HOOK_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        limits=httpx.Limits(max_connections=10),
        follow_redirects=False,
    ) as client:
        yield CrashTestHooks(service, client, endpoint=endpoint, timeout_seconds=timeout)
