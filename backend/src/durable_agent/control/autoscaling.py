"""Control's single worker-pool reconciler; Docker calls never hold a DB transaction."""

import asyncio
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError

from durable_agent.control.capacity import CapacityPolicy, worker_demand
from durable_agent.control.worker_pool import DockerWorkers, WorkerPoolError
from durable_agent.crash_test import CrashTestHooks, HookInterrupted
from durable_agent.db.session import Database

logger = logging.getLogger(__name__)


async def scale_workers(
    db: Database,
    workers: DockerWorkers,
    *,
    hooks: CrashTestHooks | None = None,
    interval: float = 5,
    down_delay: float = 60,
) -> None:
    policy = CapacityPolicy(workers.maximum, down_delay=down_delay)
    previous_target = None
    while True:
        try:
            if hooks:
                await hooks.hit("control.before_scale")
            async with db.session() as session:
                demand = await worker_demand(session)
            current = len(await workers.inventory())
            desired = policy.desired(demand, current, asyncio.get_running_loop().time())
            await workers.resize(desired)
            if desired != previous_target:
                logger.info("Worker pool target: %s; demand: %s", desired, demand)
                previous_target = desired
        except HookInterrupted:
            policy.underused_since = None
        except (SQLAlchemyError, httpx.HTTPError, WorkerPoolError):
            # Missing observations are not evidence of idle capacity.
            policy.underused_since = None
            logger.warning("Worker scaling unavailable; retrying", exc_info=True)
        await asyncio.sleep(interval)


async def run_worker_pool(db: Database, project: str, maximum: int, hooks: CrashTestHooks) -> None:
    async with httpx.AsyncClient(
        base_url="http://docker",
        transport=httpx.AsyncHTTPTransport(uds="/var/run/docker.sock"),
        timeout=10,
    ) as client:
        await scale_workers(db, DockerWorkers(client, project, maximum), hooks=hooks)
