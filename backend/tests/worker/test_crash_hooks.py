import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from durable_agent.crash_test import CrashTestHooks
from durable_agent.worker.lease import LeaseLost

from .test_runner import exercise


@pytest.mark.parametrize("boundary", ["worker.after_claim", "worker.before_report"])
def test_interrupt_execution_or_abandon_delivery(assignment, response_payload, boundary):
    async def run():
        events = []

        def controller(request):
            event = json.loads(request.content)
            events.append(event)
            return httpx.Response(
                200, json={"action": "interrupt" if event["name"] == boundary else "continue"}
            )

        async with httpx.AsyncClient(
            base_url="http://controller", transport=httpx.MockTransport(controller)
        ) as client:
            execute, requests, _ = exercise(
                assignment, response_payload, hooks=CrashTestHooks("worker", client)
            )
            if boundary == "worker.before_report":
                with pytest.raises(LeaseLost, match="Crash Test"):
                    await execute()
                assert len(requests["model"]) == 1
                assert requests["report"] == []
            else:
                await execute()
                assert requests["model"] == []
                outcome = requests["report"][0]["outcome"]
                assert outcome["error"]["code"] == "crash_test_interrupted"
                assert outcome["error"]["retryable"]
            assert [e["name"] for e in events] == ["worker.after_claim", "worker.before_report"]
            for event in events:
                assert event["run_id"] == str(assignment.run_id)
                assert event["operation_id"] == str(assignment.operation_id)
                assert event["attempt_id"] == str(assignment.attempt_id)
                assert event["worker_id"]
                assert event["lease_generation"] == assignment.lease_generation

    asyncio.run(run())


def test_held_hook_does_not_extend_action_deadline(assignment, response_payload):
    assignment.deadline_at = assignment.server_time + timedelta(seconds=0.04)

    async def run():
        cancelled = []

        async def controller(request):
            if json.loads(request.content)["name"] == "worker.after_claim":
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)
            return httpx.Response(200, json={"action": "continue"})

        async with httpx.AsyncClient(
            base_url="http://controller", transport=httpx.MockTransport(controller)
        ) as client:
            execute, requests, _ = exercise(
                assignment, response_payload, hooks=CrashTestHooks("worker", client)
            )
            await execute()
            assert cancelled == [True]
            assert requests["model"] == []
            assert requests["heartbeat"]
            assert requests["report"][0]["outcome"]["error"]["code"] == "call_timeout"

    asyncio.run(run())
