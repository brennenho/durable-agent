import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from durable_agent.client import ApiError, Client

from .test_client import RUN_ID, detail


def summary(**extra):
    value = detail(**extra)
    for key in ("final_answer", "workspace_version_id", "operations"):
        value.pop(key)
    return value


def test_pause_and_resume_return_persisted_status():
    requested = []

    def respond(request):
        requested.append(request)
        action = request.url.path.rsplit("/", 1)[-1]
        if action == "pause":
            return httpx.Response(200, json=summary(status="running", pause_requested=True))
        if action == "resume":
            return httpx.Response(200, json=summary(status="retrying", pause_requested=False))
        return httpx.Response(200, json=detail())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                paused = await run.pause()
                assert paused.pause_requested and paused.status == "running"
                resumed = await run.resume()
                assert not resumed.pause_requested and resumed.status == "retrying"

    asyncio.run(scenario())
    assert [(r.method, r.url.path) for r in requested[1:]] == [
        ("POST", f"/api/runs/{RUN_ID}/pause"),
        ("POST", f"/api/runs/{RUN_ID}/resume"),
    ]


def test_fork_returns_new_run_and_reuses_explicit_submission_key():
    operation_id, submission_key, branch_id = uuid4(), uuid4(), uuid4()
    submissions = []

    def respond(request):
        if request.method == "POST":
            assert request.url.path == f"/api/runs/{RUN_ID}/forks"
            submissions.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": str(branch_id),
                    "title": "Try another approach",
                    "status": "queued",
                    "created_at": "2026-10-02T00:00:00Z",
                    "updated_at": "2026-10-02T00:00:00Z",
                    "latest_run_id": str(branch_id),
                },
            )
        return httpx.Response(200, json=detail())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                for _ in range(2):
                    branch = await run.fork(
                        prompt="Try another approach",
                        after_operation_id=operation_id,
                        submission_key=submission_key,
                    )
                    assert branch.id == branch_id
                    assert run.id == RUN_ID

    asyncio.run(scenario())
    assert (
        submissions
        == [
            {
                "prompt": "Try another approach",
                "after_operation_id": str(operation_id),
                "submission_key": str(submission_key),
            }
        ]
        * 2
    )


@pytest.mark.parametrize("action", ["pause", "resume", "fork"])
def test_server_control_conflicts_are_explicit(action):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "child_run_read_only",
                        "message": "Use the parent run",
                        "retryable": False,
                    }
                },
            )
        return httpx.Response(200, json=detail())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                with pytest.raises(ApiError) as failure:
                    if action == "fork":
                        await run.fork(
                            prompt="branch", after_operation_id=uuid4(), submission_key=uuid4()
                        )
                    else:
                        await getattr(run, action)()
                assert failure.value.code == "child_run_read_only"

    asyncio.run(scenario())
    assert len(requests) == 2
