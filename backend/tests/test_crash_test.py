import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from durable_agent.crash_test import CrashTestHooks, HookInterrupted, open_hooks


def test_disabled_hooks_make_no_requests(monkeypatch):
    monkeypatch.delenv("CRASH_TEST_HOOK_URL", raising=False)

    async def exercise():
        async with open_hooks("tools") as hooks:
            assert hooks.client is None
            await hooks.hit("tools.after_commit", operation_id=uuid4())

    asyncio.run(exercise())


@pytest.mark.parametrize("action", ["continue", "interrupt"])
def test_hook_holds_at_boundary_and_transmits_identity(action):
    async def exercise():
        reached, release = asyncio.Event(), asyncio.Event()
        operation, attempt = uuid4(), uuid4()
        events = []

        async def controller(request):
            assert request.url.path == "/hooks"
            events.append(json.loads(request.content))
            reached.set()
            await release.wait()
            return httpx.Response(200, json={"action": action})

        async with httpx.AsyncClient(
            base_url="http://crash-test", transport=httpx.MockTransport(controller)
        ) as client:
            hooks = CrashTestHooks("tools", client)
            pending = asyncio.create_task(
                hooks.hit(
                    "tools.after_commit",
                    operation_id=operation,
                    attempt_id=attempt,
                    lease_generation=2,
                )
            )
            await asyncio.wait_for(reached.wait(), 1)
            assert not pending.done()
            event = events[0]
            assert event["operation_id"] == str(operation)
            assert event["attempt_id"] == str(attempt)
            assert event["service"] == "tools"
            assert event["lease_generation"] == 2
            assert event["instance"] and event["pid"] and event["observed_at"] and event["event_id"]
            assert "run_id" not in event
            release.set()
            if action == "interrupt":
                with pytest.raises(HookInterrupted, match="tools.after_commit"):
                    await pending
            else:
                await pending

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["offline", "http", "invalid", "timeout"])
def test_observer_failure_is_bounded_logged_and_does_not_inject_fault(failure, caplog):
    async def controller(request):
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "timeout":
            await asyncio.Event().wait()
        return httpx.Response(503 if failure == "http" else 200, json={"action": "kill_everything"})

    async def exercise():
        async with httpx.AsyncClient(
            base_url="http://controller", transport=httpx.MockTransport(controller)
        ) as client:
            await asyncio.wait_for(
                CrashTestHooks("worker", client, timeout_seconds=0.01).hit("worker.after_claim"), 1
            )

    asyncio.run(exercise())
    assert "was not acknowledged" in caplog.text


def test_cancellation_is_propagated_to_pending_observer():
    async def exercise():
        reached, cancelled = asyncio.Event(), asyncio.Event()

        async def controller(request):
            reached.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with httpx.AsyncClient(
            base_url="http://controller", transport=httpx.MockTransport(controller)
        ) as client:
            task = asyncio.create_task(CrashTestHooks("worker", client).hit("worker.after_claim"))
            await asyncio.wait_for(reached.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize("timeout", [0, -1, 31, float("nan")])
def test_hook_wait_cannot_be_unbounded(timeout):
    with pytest.raises(ValueError):
        CrashTestHooks("tools", timeout_seconds=timeout)


def test_environment_uses_exact_endpoint_token_and_closes_client(monkeypatch):
    monkeypatch.setenv("CRASH_TEST_HOOK_URL", "http://controller/custom/hook")
    monkeypatch.setenv("CRASH_TEST_HOOK_TOKEN", "fixture-token")
    original = httpx.AsyncClient
    clients = []

    def controller(request):
        assert str(request.url) == "http://controller/custom/hook"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        return httpx.Response(200, json={"action": "continue"})

    def client(**kwargs):
        clients.append(original(**kwargs, transport=httpx.MockTransport(controller)))
        return clients[-1]

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def exercise():
        async with open_hooks("tools") as hooks:
            await hooks.hit("tools.after_commit", operation_id=uuid4())
        assert clients[0].is_closed

    asyncio.run(exercise())
