import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from durable_agent.worker.lease import Lease, LeaseLost
from durable_agent.worker.services import ServiceClient


def services(handler):
    return ServiceClient(
        "http://control",
        "http://tools",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize("budget", [60, 120, 630])
def test_budget_uses_server_durations_and_claim_start(assignment, budget):
    assert assignment.config.call_timeout_seconds == 120
    assignment.deadline_at = assignment.server_time + timedelta(seconds=budget)
    lease = Lease(None, assignment, uuid4(), claimed_at=100)
    assert lease.expires == 130
    assert lease.deadline == 100 + budget


def test_late_claim_never_starts_execution(assignment):
    async def exercise():
        lease = Lease(None, assignment, uuid4(), claimed_at=asyncio.get_running_loop().time() - 31)

        async def work():
            pytest.fail("Expired work must not start")

        with pytest.raises(LeaseLost):
            await lease.guard(work)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "failure", ["stale-generation", "heartbeat-stalls", "changed-deadline", "invalid-response"]
)
def test_lease_loss_cancels_execution_and_heartbeat(assignment, failure):
    async def exercise():
        stopped = asyncio.Event()
        request_stopped = asyncio.Event()

        async def handler(request):
            try:
                if failure == "heartbeat-stalls":
                    await asyncio.Future()
                if failure == "invalid-response":
                    return httpx.Response(200, content="{broken}")
                if failure == "changed-deadline":
                    return httpx.Response(
                        200,
                        json={
                            "server_time": assignment.server_time.isoformat(),
                            "lease_expires_at": assignment.lease_expires_at.isoformat(),
                            "deadline_at": (
                                assignment.deadline_at + timedelta(seconds=1)
                            ).isoformat(),
                        },
                    )
                return httpx.Response(
                    409,
                    json={
                        "error": {"code": "stale_lease", "message": "Expired", "retryable": False}
                    },
                )
            finally:
                request_stopped.set()

        async def work():
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        client = services(handler)
        assignment.lease_expires_at = assignment.server_time + timedelta(seconds=0.1)
        lease = Lease(
            client,
            assignment,
            uuid4(),
            claimed_at=asyncio.get_running_loop().time(),
            heartbeat_interval=0.005,
        )
        try:
            async with asyncio.timeout(2):
                with pytest.raises(LeaseLost):
                    await lease.guard(work)
            assert stopped.is_set()
            assert request_stopped.is_set()
        finally:
            await client.close()

    asyncio.run(exercise())


def test_parent_cancellation_cleans_up_execution(assignment):
    async def exercise():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        lease = Lease(None, assignment, uuid4(), claimed_at=asyncio.get_running_loop().time())
        task = asyncio.create_task(lease.guard(work))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize("budget", [120, 630])
def test_transient_heartbeat_failure_can_recover_without_extending_call_deadline(
    assignment, budget
):
    assignment.deadline_at = assignment.server_time + timedelta(seconds=budget)

    async def exercise():
        renewed = asyncio.Event()
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    503,
                    json={
                        "error": {
                            "code": "database_unavailable",
                            "message": "Unavailable",
                            "retryable": True,
                        }
                    },
                )
            renewed.set()
            return httpx.Response(
                200,
                json={
                    "server_time": assignment.server_time.isoformat(),
                    "deadline_at": assignment.deadline_at.isoformat(),
                    "lease_expires_at": (
                        assignment.server_time + timedelta(seconds=60)
                    ).isoformat(),
                },
            )

        async def work():
            await renewed.wait()
            return "completed"

        client = services(handler)
        lease = Lease(
            client,
            assignment,
            uuid4(),
            claimed_at=asyncio.get_running_loop().time(),
            heartbeat_interval=0.005,
        )
        deadline, expires = lease.deadline, lease.expires
        try:
            async with asyncio.timeout(2):
                assert await lease.guard(work) == "completed"
            assert lease.deadline == deadline
            assert lease.expires > expires
        finally:
            await client.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [200, 503])
def test_heartbeats_do_not_accelerate_as_expiry_approaches(assignment, status):
    async def exercise():
        requests = []
        assignment.lease_expires_at = assignment.server_time + timedelta(seconds=0.1)
        assignment.deadline_at = assignment.lease_expires_at

        def handler(request):
            requests.append(request)
            # A renewal capped by the fixed deadline cannot extend ownership.
            now = datetime.now(UTC)
            return httpx.Response(
                status,
                json={
                    "server_time": now.isoformat(),
                    "lease_expires_at": assignment.lease_expires_at.isoformat(),
                    "deadline_at": assignment.deadline_at.isoformat(),
                },
            )

        async def work():
            await asyncio.Future()

        client = services(handler)
        lease = Lease(
            client,
            assignment,
            uuid4(),
            claimed_at=asyncio.get_running_loop().time(),
            heartbeat_interval=0.03,
        )
        try:
            with pytest.raises(LeaseLost):
                await lease.guard(work)
            assert 0 < len(requests) <= 3
        finally:
            await client.close()

    asyncio.run(exercise())
