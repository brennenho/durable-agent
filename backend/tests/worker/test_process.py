import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from durable_agent.worker import __main__ as process
from durable_agent.worker.lease import LeaseLost
from durable_agent.worker.runner import Worker
from durable_agent.worker.services import ServiceClient, ServiceError


def test_polling_recovers_from_outage_and_idle_without_overlapping_work(assignment):
    async def exercise():
        worker_id = uuid4()
        claims, executions = [], []

        async def handler(request):
            assert json.loads(request.content) == {"worker_id": str(worker_id)}
            claims.append(request)
            if len(claims) == 1:
                return httpx.Response(503)
            if len(claims) == 2:
                return httpx.Response(200, content="null")
            if len(claims) < 5:
                return httpx.Response(200, json=assignment.model_dump(mode="json"))
            raise asyncio.CancelledError

        services = ServiceClient(
            "http://control",
            "http://tools",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        worker = Worker(services, None, worker_id, poll_interval=0.001)

        async def execute(claimed, *, claimed_at):
            calls_before = len(claims)
            assert claimed == assignment
            assert claimed_at <= asyncio.get_running_loop().time()
            await asyncio.sleep(0.005)
            assert len(claims) == calls_before
            executions.append(claimed)
            if len(executions) == 1:
                raise LeaseLost("Expired")

        worker.run_assignment = execute
        try:
            with pytest.raises(asyncio.CancelledError):
                await worker.run()
            assert len(claims) == 5
            assert len(executions) == 2
        finally:
            await services.close()

    asyncio.run(exercise())


def test_permanent_claim_failure_stops_the_worker():
    async def exercise():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(401)

        services = ServiceClient(
            "http://control",
            "http://tools",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        try:
            with pytest.raises(ServiceError):
                await Worker(services, None, uuid4()).run()
            assert len(requests) == 1
        finally:
            await services.close()

    asyncio.run(exercise())


def test_startup_requires_key_before_opening_clients(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        asyncio.run(process.main())


@pytest.mark.parametrize("url", ["", "/relative", "ftp://host"])
def test_service_url_must_be_http(monkeypatch, url):
    monkeypatch.setenv("CONTROL_URL", url)
    with pytest.raises(ValueError, match="CONTROL_URL"):
        process.service_url("CONTROL_URL", "http://localhost:8000")


def test_shutdown_closes_both_clients_and_propagates_cancellation(monkeypatch):
    closed = []

    class Client:
        def __init__(self, *args):
            pass

        async def close(self):
            closed.append(self)

    class CancelledWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            raise asyncio.CancelledError

        def stop(self):
            pass

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CONTROL_URL", "http://control")
    monkeypatch.setenv("TOOLS_URL", "http://tools")
    monkeypatch.setattr(process, "ServiceClient", Client)
    monkeypatch.setattr(process, "OpenAIClient", Client)
    monkeypatch.setattr(process, "Worker", CancelledWorker)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(process.main())
    assert len(closed) == 2
