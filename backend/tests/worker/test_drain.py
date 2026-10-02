import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from durable_agent.worker.openai_client import OpenAIClient
from durable_agent.worker.runner import Worker
from durable_agent.worker.services import ServiceClient


@pytest.mark.parametrize("stop_during", ["claim", "model", "report"])
def test_drain_finishes_claim_and_report_without_claiming_again(
    assignment, response_payload, stop_during
):
    async def exercise():
        calls = {"claim": 0, "model": 0, "report": 0, "heartbeat": 0}
        heartbeat = asyncio.Event()

        async def handler(request):
            if request.url.path == "/internal/claims":
                calls["claim"] += 1
                if stop_during == "claim":
                    worker.stop()
                return httpx.Response(200, json=assignment.model_dump(mode="json"))
            if request.url.host == "api.openai.com":
                calls["model"] += 1
                if stop_during == "model":
                    worker.stop()
                await heartbeat.wait()
                return httpx.Response(200, json=response_payload)
            if request.url.path.endswith("heartbeat"):
                calls["heartbeat"] += 1
                heartbeat.set()
                now = datetime.now(UTC)
                return httpx.Response(
                    200,
                    json={
                        "server_time": now.isoformat(),
                        "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
                        "deadline_at": assignment.deadline_at.isoformat(),
                    },
                )
            calls["report"] += 1
            if stop_during == "report":
                worker.stop()
            if calls["report"] == 1:
                raise httpx.ReadError("Acknowledgment lost", request=request)
            return httpx.Response(200, json={"status": "already_recorded"})

        transport = httpx.MockTransport(handler)
        services = ServiceClient(
            "http://control", "http://tools", http_client=httpx.AsyncClient(transport=transport)
        )
        models = OpenAIClient("test-key", http_client=httpx.AsyncClient(transport=transport))
        worker = Worker(
            services, models, uuid4(), heartbeat_interval=0.001, report_retry_interval=0.001
        )
        try:
            async with asyncio.timeout(1):
                await worker.run()
            assert calls["claim"] == calls["model"] == 1
            assert calls["report"] == 2
            assert calls["heartbeat"] >= 1
        finally:
            await services.close()
            await models.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [200, 503])
def test_drain_interrupts_idle_poll_or_outage_backoff(status):
    async def exercise():
        claimed = asyncio.Event()

        async def handler(request):
            claimed.set()
            return httpx.Response(status, content="null")

        services = ServiceClient(
            "http://control",
            "http://tools",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        worker = Worker(services, None, uuid4(), poll_interval=60)
        task = asyncio.create_task(worker.run())
        try:
            await claimed.wait()
            worker.stop()
            await asyncio.wait_for(task, timeout=1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await services.close()

    asyncio.run(exercise())
