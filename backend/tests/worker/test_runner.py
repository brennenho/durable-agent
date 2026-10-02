import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from durable_agent.contracts import ToolOperation
from durable_agent.worker.lease import LeaseLost
from durable_agent.worker.openai_client import OpenAIClient
from durable_agent.worker.runner import Worker
from durable_agent.worker.services import ServiceClient


def exercise(
    assignment,
    response_payload,
    *,
    report_failure=None,
    stall_model=False,
    stall_tool=False,
    tool_status=200,
):
    requests = {"model": [], "tool": [], "tool_timeouts": [], "heartbeat": [], "report": []}
    cancelled = []

    async def run():
        async def handler(request):
            body = json.loads(request.content)
            if request.url.host == "api.openai.com":
                requests["model"].append(body)
                if stall_model:
                    try:
                        await asyncio.Future()
                    finally:
                        cancelled.append(True)
                return httpx.Response(
                    200, json=response_payload, headers={"x-request-id": "req_run"}
                )
            if request.url.path == "/tools/execute":
                requests["tool"].append(body)
                requests["tool_timeouts"].append(request.extensions["timeout"]["read"])
                if stall_tool:
                    try:
                        await asyncio.Future()
                    finally:
                        cancelled.append(True)
                return httpx.Response(
                    tool_status,
                    json={
                        "output": {"status": "error", "code": "not_found", "message": "Missing"},
                        "workspace_version_id": body["input_version_id"],
                    },
                )
            if request.url.path.endswith("/heartbeat"):
                requests["heartbeat"].append(body)
                now = datetime.now(UTC)
                return httpx.Response(
                    200,
                    json={
                        "server_time": now.isoformat(),
                        "lease_expires_at": (now + timedelta(seconds=1)).isoformat(),
                        "deadline_at": assignment.deadline_at.isoformat(),
                    },
                )
            requests["report"].append(body)
            if report_failure == "unavailable":
                return httpx.Response(503)
            if report_failure == "stale":
                return httpx.Response(409)
            if report_failure == "lost-ack" and len(requests["report"]) == 1:
                raise httpx.ReadError("Report committed, acknowledgment lost", request=request)
            if report_failure == "invalid-ack" and len(requests["report"]) == 1:
                return httpx.Response(200, content="{broken}")
            return httpx.Response(
                200, json={"status": "already_recorded" if report_failure else "recorded"}
            )

        services = ServiceClient(
            "http://control",
            "http://tools",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        models = OpenAIClient(
            "test-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        worker = Worker(
            services, models, uuid4(), heartbeat_interval=0.005, report_retry_interval=0.005
        )
        try:
            async with asyncio.timeout(2):
                await worker.run_assignment(
                    assignment, claimed_at=asyncio.get_running_loop().time()
                )
        finally:
            await models.close()
            await services.close()

    return run, requests, cancelled


@pytest.mark.parametrize("report_failure", ["lost-ack", "invalid-ack"])
def test_report_retry_reuses_outcome_without_repeating_model(
    assignment, response_payload, report_failure
):
    run, requests, _ = exercise(assignment, response_payload, report_failure=report_failure)
    asyncio.run(run())
    assert len(requests["model"]) == 1
    assert len(requests["report"]) == 2
    assert requests["report"][0] == requests["report"][1]
    assert requests["report"][0]["outcome"]["provider_request_id"] == "req_run"


@pytest.mark.parametrize("kind", ["model", "tool"])
def test_call_deadline_wins_even_while_heartbeats_succeed(assignment, response_payload, kind):
    if kind == "tool":
        assignment.operation = command_operation()
    assignment.deadline_at = assignment.server_time + timedelta(seconds=0.04)
    run, requests, cancelled = exercise(
        assignment, response_payload, stall_model=kind == "model", stall_tool=kind == "tool"
    )
    asyncio.run(run())
    assert requests["heartbeat"]
    assert cancelled == [True]
    outcome = requests["report"][0]["outcome"]
    assert outcome["status"] == "failed"
    assert outcome["error"]["code"] == "call_timeout"
    assert outcome["error"]["retryable"]
    assert "result" not in outcome


def command_operation():
    return ToolOperation.model_validate(
        {
            "kind": "tool",
            "request": {
                "call": {
                    "call_id": "call_command",
                    "name": "run_command",
                    "arguments": {"command": "pytest"},
                },
                "input_version_id": uuid4(),
            },
        }
    )


def test_tool_transport_receives_the_larger_assignment_budget(assignment, response_payload):
    assignment.operation = command_operation()
    assignment.deadline_at = assignment.server_time + timedelta(seconds=630)
    run, requests, _ = exercise(assignment, response_payload)
    asyncio.run(run())
    assert assignment.config.call_timeout_seconds == 120
    assert len(requests["tool_timeouts"]) == 1
    assert assignment.config.call_timeout_seconds < requests["tool_timeouts"][0] <= 630
    assert datetime.fromisoformat(requests["tool"][0]["deadline_at"]) == assignment.deadline_at
    assert requests["report"][0]["outcome"]["status"] == "succeeded"


def test_expired_call_budget_does_not_issue_a_model_request(assignment, response_payload):
    assignment.deadline_at = assignment.server_time - timedelta(seconds=1)
    run, requests, _ = exercise(assignment, response_payload)
    asyncio.run(run())
    assert not requests["model"]
    assert requests["report"][0]["outcome"]["error"]["code"] == "call_timeout"


def test_report_outage_cannot_renew_ownership_forever(assignment, response_payload):
    assignment.lease_expires_at = assignment.server_time + timedelta(seconds=0.06)
    run, requests, _ = exercise(assignment, response_payload, report_failure="unavailable")
    with pytest.raises(LeaseLost, match="delivery budget"):
        asyncio.run(run())
    assert len(requests["model"]) == 1
    assert len(requests["report"]) > 1
    assert requests["heartbeat"]


@pytest.mark.parametrize(
    "tool_status,report_failure", [(200, None), (200, "lost-ack"), (409, None), (503, None)]
)
def test_tool_execution_is_not_repeated_when_reporting(
    assignment, response_payload, tool_status, report_failure
):
    assignment.operation = ToolOperation.model_validate(
        {
            "kind": "tool",
            "request": {
                "call": {
                    "call_id": "call_read",
                    "name": "read_file",
                    "arguments": {"path": "missing.txt"},
                },
                "input_version_id": uuid4(),
            },
        }
    )
    run, requests, _ = exercise(
        assignment, response_payload, tool_status=tool_status, report_failure=report_failure
    )
    if tool_status == 409:
        with pytest.raises(LeaseLost):
            asyncio.run(run())
        assert not requests["report"]
    else:
        asyncio.run(run())
        outcome = requests["report"][0]["outcome"]
        assert outcome["status"] == ("succeeded" if tool_status == 200 else "failed")
        if tool_status == 200:
            assert outcome["result"]["call_id"] == "call_read"
            assert outcome["result"]["output"]["code"] == "not_found"
        else:
            assert outcome["error"]["retryable"]
        if report_failure:
            assert len(requests["report"]) == 2
            assert requests["report"][0] == requests["report"][1]
    assert len(requests["tool"]) == 1
    assert not requests["model"]


def test_stale_report_is_abandoned_without_repeating_work(assignment, response_payload):
    run, requests, _ = exercise(assignment, response_payload, report_failure="stale")
    with pytest.raises(LeaseLost):
        asyncio.run(run())
    assert len(requests["model"]) == len(requests["report"]) == 1
