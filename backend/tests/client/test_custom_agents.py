import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from durable_agent.client import Client

from .test_client import RUN_ID, detail
from .test_controls import summary

AGENT = {"name": "writer", "version": "1", "app_ref": "example:app", "build_id": "build-1"}


@pytest.mark.parametrize("output", [{"document": "Done"}, [1, 2], False, None])
def test_custom_input_and_output_preserve_json_values(output):
    submission_key = uuid4()

    def respond(request):
        if request.method == "POST":
            assert json.loads(request.content) == {
                "submission_key": str(submission_key),
                "agent": "writer",
                "input": None,
            }
            return httpx.Response(200, json=summary(agent=AGENT))
        return httpx.Response(200, json=detail("succeeded", agent=AGENT, output=output))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.start(
                    agent="writer", input=None, submission_key=submission_key
                )
                assert await run.result() == output

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fields",
    [{}, {"agent": "writer"}, {"prompt": "text", "input": {}}, {"prompt": "text", "input": None}],
)
def test_invalid_input_is_rejected_before_http(fields):
    def unexpected(request):
        pytest.fail(f"Unexpected request: {request.url}")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            async with Client("http://runtime", http_client=http) as client:
                with pytest.raises(ValueError):
                    await client.runs.start(submission_key=uuid4(), **fields)

    asyncio.run(scenario())


def test_follow_up_uses_real_conversation_and_fixed_predecessor():
    conversation_id, next_id, key = uuid4(), uuid4(), uuid4()
    submissions = []

    def respond(request):
        if request.method == "POST":
            assert request.url.path == f"/api/conversations/{conversation_id}/runs"
            submissions.append(json.loads(request.content))
            return httpx.Response(
                200, json=summary(id=str(next_id), conversation_id=str(conversation_id))
            )
        return httpx.Response(200, json=detail("succeeded", conversation_id=str(conversation_id)))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                for _ in range(2):
                    next_run = await run.follow_up(submission_key=key, input={"revision": 2})
                    assert next_run.id == next_id

    asyncio.run(scenario())
    assert (
        submissions
        == [
            {
                "submission_key": str(key),
                "input": {"revision": 2},
                "after_run_id": str(RUN_ID),
            }
        ]
        * 2
    )


def test_follow_up_does_not_guess_missing_conversation_id():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=detail())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                with pytest.raises(RuntimeError, match="conversation_id"):
                    await run.follow_up(submission_key=uuid4(), prompt="Continue")

    asyncio.run(scenario())
    assert all(r.method == "GET" for r in requests)


def test_fork_preserves_null_input():
    operation_id, branch_id, key = uuid4(), uuid4(), uuid4()

    def respond(request):
        if request.method == "POST":
            assert json.loads(request.content) == {
                "submission_key": str(key),
                "input": None,
                "after_operation_id": str(operation_id),
            }
            return httpx.Response(
                200,
                json={
                    "id": str(branch_id),
                    "title": "Branch",
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
                branch = await run.fork(
                    submission_key=key, after_operation_id=operation_id, input=None
                )
                assert branch.id == branch_id

    asyncio.run(scenario())
