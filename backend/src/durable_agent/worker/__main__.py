"""Run one assignment at a time; scale by starting more worker processes."""

import asyncio
import logging
import os
import signal
from contextlib import AsyncExitStack
from uuid import uuid4

import httpx

from durable_agent.worker.openai_client import OpenAIClient
from durable_agent.worker.runner import Worker
from durable_agent.worker.services import ServiceClient


def service_url(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    url = httpx.URL(value)
    if url.scheme not in {"http", "https"} or not url.host:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")
    return value


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key.strip():
        raise ValueError("OPENAI_API_KEY is required")
    control = service_url("CONTROL_URL", "http://localhost:8000")
    tools = service_url("TOOLS_URL", "http://localhost:8001")
    async with AsyncExitStack() as cleanup:
        services = ServiceClient(control, tools)
        cleanup.push_async_callback(services.close)
        models = OpenAIClient(api_key)
        cleanup.push_async_callback(models.close)
        worker = Worker(services, models, uuid4())
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await worker.run()
        finally:
            loop.remove_signal_handler(signal.SIGTERM)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # Signals cancel work and close clients before the process exits.
