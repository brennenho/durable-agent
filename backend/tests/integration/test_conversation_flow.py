"""Real Control/Tools/Worker flow; only the provider's HTTP response is a fixture."""

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from durable_agent.contracts import FailedOutcome, ReportRequest
from durable_agent.control.app import create_app
from durable_agent.control.runtime import Settings
from durable_agent.tools.app import app as tools_app
from durable_agent.worker.openai_client import OpenAIClient
from durable_agent.worker.runner import Worker
from durable_agent.worker.services import ServiceClient


def response(output, suffix):
    fixture = Path(__file__).parents[1] / "worker/fixtures/response.json"
    payload = json.loads(fixture.read_text())
    payload.update(id=f"resp_{suffix}", output=output)
    return payload


def call(call_id, content):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": "write_file",
        "arguments": json.dumps({"path": "report.md", "content": content}),
        "status": "completed",
    }


def answer(text):
    return [
        {
            "type": "message",
            "id": f"msg_{uuid4().hex}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    ]


@asynccontextmanager
async def running_services(settings, provider, lost_acks):
    tools_transport = httpx.ASGITransport(app=tools_app)
    control = create_app(settings, transport=tools_transport)
    control_transport = httpx.ASGITransport(app=control)

    async def forward(request):
        transport = tools_transport if request.url.host == "tools" else control_transport
        result = await transport.handle_async_request(request)
        if request.url.path.endswith("/report") and not lost_acks:
            lost_acks.append(request.url.path)
            raise httpx.ReadError("Report committed; acknowledgement lost", request=request)
        return result

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(tools_app.router.lifespan_context(tools_app))
        await stack.enter_async_context(control.router.lifespan_context(control))
        services = ServiceClient(
            "http://control",
            "http://tools",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(forward)),
        )
        stack.push_async_callback(services.close)
        models = OpenAIClient(
            "fixture-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        )
        stack.push_async_callback(models.close)
        api = await stack.enter_async_context(
            httpx.AsyncClient(transport=control_transport, base_url="http://control")
        )
        yield api, services, Worker(services, models, uuid4(), report_retry_interval=0)


@pytest.mark.parametrize("first_status", ["succeeded", "failed"])
def test_followup_restores_accepted_context_and_files_after_restart(
    postgres_engine, monkeypatch, first_status
):
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    for name in ("WORKSPACE_INPUT_DIR", "SANDBOX_WORKSPACE_ROOT", "SANDBOX_IMAGE_DIGEST"):
        monkeypatch.delenv(name, raising=False)
    model_requests, lost_acks = [], []
    first_calls = [call("saved", "First\n")]
    plan = [
        response(first_calls, "first"),
        response(
            answer("First answer")
            if first_status == "succeeded"
            else [call("unaccepted", "Discard\n")],
            "first_final",
        ),
        response([call("followup", "First\nSecond\n")], "followup"),
        response(answer("Follow-up answer"), "followup_final"),
    ]

    def provider(request):
        model_requests.append(json.loads(request.content))
        return httpx.Response(200, json=deepcopy(plan.pop(0)))

    async def exercise():
        initial_id = initial_version = followup_id = None
        first_detail = None
        for phase in range(2):
            settings = Settings(
                database_url=database_url,
                tools_url="http://tools",
                model="original-model" if phase == 0 else "new-default-model",
                image_digest="sha256:" + "a" * 64,
            )
            async with running_services(settings, provider, lost_acks) as (api, services, worker):
                if phase == 0:
                    created = await api.post(
                        "/api/conversations",
                        json={
                            "submission_key": str(uuid4()),
                            "prompt": "Write the first report",
                        },
                    )
                    assert created.status_code == 200, created.text
                    initial_id = created.json()["id"]
                else:
                    body = {
                        "submission_key": str(uuid4()),
                        "prompt": "Add a second line",
                        "after_run_id": initial_id,
                    }
                    created = await api.post(f"/api/conversations/{initial_id}/runs", json=body)
                    assert created.status_code == 200, created.text
                    followup_id = created.json()["id"]
                    replayed = await api.post(f"/api/conversations/{initial_id}/runs", json=body)
                    assert replayed.json()["id"] == followup_id
                for step in range(3):
                    claimed_at = asyncio.get_running_loop().time()
                    assignment = await services.claim(worker.worker_id)
                    assert assignment is not None
                    if phase == 1 and step == 0:
                        assert assignment.config.model == "original-model"
                        assert (
                            str(assignment.config.initial_workspace_version_id) == initial_version
                        )
                        assert (
                            assignment.operation.request.previous_turns[0].prompt
                            == "Write the first report"
                        )
                    await worker.run_assignment(assignment, claimed_at=claimed_at)
                if phase == 0:
                    if first_status == "failed":
                        # Tools commits the effect; Control never accepts that result.
                        assignment = await services.claim(worker.worker_id)
                        unaccepted = await services.execute_tool(assignment, timeout_seconds=5)
                        failed = FailedOutcome(
                            status="failed",
                            error={
                                "code": "delivery_exhausted",
                                "message": "Tool outcome could not be delivered",
                                "retryable": False,
                            },
                        )
                        await services.report(
                            assignment,
                            ReportRequest(
                                worker_id=worker.worker_id,
                                lease_generation=assignment.lease_generation,
                                outcome=failed,
                            ),
                        )
                    first_detail = (await api.get(f"/api/runs/{initial_id}")).json()
                    assert first_detail["status"] == first_status
                    initial_version = first_detail["workspace_version_id"]
                    if first_status == "failed":
                        assert str(unaccepted.result.workspace_version_id) != initial_version
                else:
                    conversation = (await api.get(f"/api/conversations/{initial_id}")).json()
                    assert [run["id"] for run in conversation["runs"]] == [
                        initial_id,
                        followup_id,
                    ]
                    assert conversation["runs"][0] == first_detail
                    final = conversation["runs"][1]
                    assert final["status"] == "succeeded"
                    assert final["final_answer"] == "Follow-up answer"
                    for run_id, version, expected in [
                        (initial_id, initial_version, b"First\n"),
                        (followup_id, final["workspace_version_id"], b"First\nSecond\n"),
                    ]:
                        download = await api.get(
                            f"/api/runs/{run_id}/file",
                            params={"version_id": version, "path": "report.md"},
                        )
                        assert download.status_code == 200
                        assert download.content == expected
                    assert len(conversation["runs"][0]["operations"][0]["attempts"]) == 1
        assert not plan
        followup_input = model_requests[2]["input"]
        assert [item["content"] for item in followup_input if item.get("role") == "user"] == [
            "Write the first report",
            "Add a second line",
        ]
        assert first_calls[0] in followup_input
        saved_result = next(
            item
            for item in followup_input
            if item.get("type") == "function_call_output" and item["call_id"] == "saved"
        )
        assert json.loads(saved_result["output"])["status"] == "ok"
        if first_status == "failed":
            interrupted = next(
                item
                for item in followup_input
                if item.get("type") == "function_call_output" and item["call_id"] == "unaccepted"
            )
            assert json.loads(interrupted["output"])["code"] == "interrupted"
        assert len(lost_acks) == 1
        assert len(model_requests) == 4

    asyncio.run(exercise())
