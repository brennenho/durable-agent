"""Worker entry point for the disposable recorded Crash Test stack only."""

import asyncio
import logging
import os
import signal
from contextlib import AsyncExitStack
from uuid import uuid4

from durable_agent.crash_test import open_hooks
from durable_agent.crash_testing.docker import DockerControl
from durable_agent.crash_testing.recorded import RecordedModel
from durable_agent.worker.runner import Worker
from durable_agent.worker.services import ServiceClient


async def main():
    DockerControl(os.environ["CRASH_TEST_PROJECT"])
    async with AsyncExitStack() as cleanup:
        services = ServiceClient(os.environ["CONTROL_URL"], os.environ["TOOLS_URL"])
        cleanup.push_async_callback(services.close)
        hooks = await cleanup.enter_async_context(open_hooks("worker"))
        model = await asyncio.to_thread(RecordedModel)
        worker = Worker(services, model, uuid4(), hooks=hooks)
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
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
        pass
