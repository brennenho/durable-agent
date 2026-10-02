"""A command timeout returns to the model without publishing partial workspace changes."""

import asyncio
import json
import os
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from durable_agent.control.runtime import Settings

from .test_conversation_flow import answer, call, response, running_services


def test_model_adjusts_command_budget_after_timeout(postgres_engine, monkeypatch, tmp_path):
    image = os.environ.get("SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("Set SANDBOX_TEST_IMAGE to an installed, pinned Python sandbox image")
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("WORKSPACE_INPUT_DIR", raising=False)
    monkeypatch.setenv("SANDBOX_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("SANDBOX_IMAGE_DIGEST", image)
    monkeypatch.setenv("SANDBOX_OWNER", f"budget-test-{uuid4().hex}")

    def command(call_id, script, seconds):
        return {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": "run_command",
            "arguments": json.dumps({"command": script, "timeout_seconds": seconds}),
            "status": "completed",
        }

    model_requests, lost_acks = [], []
    plan = [
        response(
            [
                call("saved", "Checkpoint\n"),
                command("short", "printf partial > partial.txt; sleep 5", 1),
            ],
            "plan",
        ),
        response(
            [
                command(
                    "longer",
                    "test ! -e partial.txt && test -f report.md && printf done > done.txt",
                    600,
                )
            ],
            "adjusted",
        ),
        response(answer("Finished with a larger command budget."), "final"),
    ]

    def provider(request):
        body = json.loads(request.content)
        model_requests.append(body)
        if len(model_requests) == 2:
            returned = next(
                item
                for item in body["input"]
                if item.get("call_id") == "short" and item["type"] == "function_call_output"
            )
            assert json.loads(returned["output"])["code"] == "command_timeout"
        return httpx.Response(200, json=deepcopy(plan.pop(0)))

    async def exercise():
        settings = Settings(
            database_url=database_url, tools_url="http://tools", model="fixture", image_digest=image
        )
        async with running_services(settings, provider, lost_acks) as (api, services, worker):
            created = await api.post(
                "/api/runs",
                json={"submission_key": str(uuid4()), "prompt": "Save progress and run commands"},
            )
            assert created.status_code == 200, created.text
            run_id = created.json()["id"]
            assignments = []
            for _ in range(6):
                claimed_at = asyncio.get_running_loop().time()
                assignment = await services.claim(worker.worker_id)
                assert assignment is not None
                assignments.append(assignment)
                await worker.run_assignment(assignment, claimed_at=claimed_at)
            detail = (await api.get(f"/api/runs/{run_id}")).json()
            assert detail["status"] == "succeeded", detail
            saved, timed_out, completed = [detail["operations"][i] for i in (1, 2, 4)]
            assert timed_out["result"]["output"]["code"] == "command_timeout"
            assert (
                timed_out["result"]["workspace_version_id"]
                == saved["result"]["workspace_version_id"]
            )
            assert len(timed_out["attempts"]) == 1
            assert timed_out["attempts"][0]["status"] == "accepted"
            assert completed["result"]["output"]["value"]["exit_code"] == 0
            for index, seconds in ((0, 120), (2, 31), (4, 630)):
                assignment = assignments[index]
                assert (assignment.deadline_at - assignment.server_time).total_seconds() == seconds
            files = (await api.get(f"/api/runs/{run_id}/files")).json()
            assert {entry["path"] for entry in files["entries"]} == {"report.md", "done.txt"}
            assert len(lost_acks) == 1
            assert not plan
            schema = next(
                tool for tool in model_requests[0]["tools"] if tool["name"] == "run_command"
            )
            assert schema["parameters"]["properties"]["timeout_seconds"]["maximum"] == 900

    asyncio.run(exercise())
