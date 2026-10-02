import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from durable_agent.contracts import ReportRequest, ToolOperation
from durable_agent.sdk.types import ToolDefinition
from durable_agent.worker.services import ServiceClient, ServiceError


def client(handler):
    return ServiceClient(
        "http://control",
        "http://tools",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_claim_and_heartbeat_preserve_assignment_identity(assignment):
    worker_id = uuid4()

    def handler(request):
        assert request.url.host == "control"
        body = json.loads(request.content)
        if request.url.path == "/internal/claims":
            assert body == {"worker_id": str(worker_id)}
            return httpx.Response(200, json=assignment.model_dump(mode="json"))
        assert request.url.path == f"/internal/attempts/{assignment.attempt_id}/heartbeat"
        assert body == {"worker_id": str(worker_id), "lease_generation": 2}
        return httpx.Response(
            200,
            json={
                key: assignment.model_dump(mode="json")[key]
                for key in ("lease_expires_at", "deadline_at", "server_time")
            },
        )

    async def exercise():
        services = client(handler)
        try:
            claimed = await services.claim(worker_id)
            assert claimed == assignment
            assert (
                await services.heartbeat(claimed, worker_id)
            ).deadline_at == assignment.deadline_at
        finally:
            await services.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [200, 503, 409, 422])
def test_only_a_successful_null_claim_is_idle(status):
    async def exercise():
        services = client(lambda _: httpx.Response(status, content="null"))
        try:
            if status == 200:
                assert await services.claim(uuid4()) is None
            else:
                with pytest.raises(ServiceError) as caught:
                    await services.claim(uuid4())
                assert caught.value.status_code == status
                assert caught.value.error.retryable is (status == 503)
        finally:
            await services.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "output",
    [
        {"status": "ok", "value": {"exit_code": 1}},
        {"status": "error", "code": "not_found", "message": "Missing file"},
    ],
)
def test_tool_result_is_completed_and_uses_model_call_id(assignment, output):
    input_id, output_id = uuid4(), uuid4()
    assignment.operation = ToolOperation.model_validate(
        {
            "kind": "tool",
            "request": {
                "call": {
                    "call_id": "call_provider",
                    "name": "read_file",
                    "arguments": {"path": "a.txt"},
                },
                "input_version_id": input_id,
            },
        }
    )

    def handler(request):
        assert request.url.host == "tools"
        body = json.loads(request.content)
        assert body["operation_id"] == str(assignment.operation_id)
        assert body["input_version_id"] == str(input_id)
        assert body["lease_generation"] == 2
        assert body["deadline_at"] == assignment.model_dump(mode="json")["deadline_at"]
        assert body["sandbox"] == assignment.config.sandbox.model_dump()
        assert "call_id" not in body
        return httpx.Response(200, json={"output": output, "workspace_version_id": str(output_id)})

    async def exercise():
        services = client(handler)
        try:
            result = (await services.execute_tool(assignment, timeout_seconds=1)).result
            assert result.call_id == "call_provider"
            assert result.workspace_version_id == output_id
            assert result.output.model_dump() == output
        finally:
            await services.close()

    asyncio.run(exercise())


def test_report_preserves_outcome_and_accepts_duplicate_acknowledgment(assignment):
    report = ReportRequest(
        worker_id=uuid4(),
        lease_generation=2,
        outcome={
            "status": "failed",
            "error": {"code": "timeout", "message": "Timed out", "retryable": True},
        },
    )

    def handler(request):
        assert request.url.path == f"/internal/attempts/{assignment.attempt_id}/report"
        assert json.loads(request.content) == report.model_dump(mode="json")
        return httpx.Response(200, json={"status": "already_recorded"})

    async def exercise():
        services = client(handler)
        try:
            assert (await services.report(assignment, report)).status == "already_recorded"
        finally:
            await services.close()

    asyncio.run(exercise())


def test_service_error_keeps_retry_policy(assignment):
    error = {
        "code": "sandbox_crashed",
        "message": "Lost sandbox",
        "retryable": True,
        "retry_after_seconds": 2,
    }

    async def exercise():
        services = client(lambda _: httpx.Response(503, json={"error": error}))
        try:
            with pytest.raises(ServiceError) as caught:
                await services.heartbeat(assignment, uuid4())
            assert caught.value.error.model_dump() == error
        finally:
            await services.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("body", ["", "{broken}", "{}"])
def test_invalid_claim_response_is_not_idle(body):
    async def exercise():
        services = client(lambda _: httpx.Response(200, content=body))
        try:
            with pytest.raises(ValueError):
                await services.claim(uuid4())
        finally:
            await services.close()

    asyncio.run(exercise())


def test_service_deadline_cancels_a_response_that_keeps_streaming(assignment):
    requests, cancelled = [], []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                while True:
                    yield b" "
                    await asyncio.sleep(0.001)
            finally:
                cancelled.append(True)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=SlowBody())

    assignment.operation = ToolOperation(
        kind="tool",
        request={
            "call": {"call_id": "call_read", "name": "read_file", "arguments": {"path": "a"}},
            "input_version_id": uuid4(),
        },
    )

    async def exercise():
        services = client(handler)
        try:
            async with asyncio.timeout(1):
                with pytest.raises(ServiceError) as caught:
                    await services.execute_tool(assignment, timeout_seconds=0.02)
            assert caught.value.error.retryable
            assert len(requests) == 1
            assert cancelled == [True]
        finally:
            await services.close()

    asyncio.run(exercise())


def test_custom_tool_receives_frozen_definition_and_attempt_identity(assignment):
    definition = ToolDefinition(
        name="summarize",
        version="2",
        entrypoint="example.tools:summarize",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        takes_context=True,
    )
    assignment.config.tool_definitions[definition.name] = definition
    assignment.operation = ToolOperation(
        kind="tool",
        request={
            "call": {"call_id": "sdk-call", "name": "summarize", "arguments": {"path": "a.md"}},
            "input_version_id": assignment.config.initial_workspace_version_id,
        },
    )

    def handler(http_request):
        body = json.loads(http_request.content)
        assert body["definition"] == definition.model_dump(mode="json")
        assert body["run_id"] == str(assignment.run_id)
        assert body["attempt_id"] == str(assignment.attempt_id)
        assert body["operation_id"] == str(assignment.operation_id)
        assert body["arguments"] == {"path": "a.md"}
        assert body["deadline_at"] == assignment.deadline_at.isoformat().replace("+00:00", "Z")
        return httpx.Response(
            200,
            json={
                "output": {"status": "ok", "value": {"summary": "Saved summary"}},
                "workspace_version_id": body["input_version_id"],
            },
        )

    async def exercise():
        services = client(handler)
        try:
            result = (await services.execute_tool(assignment, timeout_seconds=1)).result
            assert result.call_id == "sdk-call"
            assert result.output.value == {"summary": "Saved summary"}
        finally:
            await services.close()

    asyncio.run(exercise())
