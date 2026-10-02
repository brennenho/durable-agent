import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import httpx
import pytest
from durable_agent.client import ApiError, Client, RunFailed

RUN_ID = uuid4()


def detail(status="queued", **extra):
    return {
        "id": str(RUN_ID),
        "prompt": "Write a document",
        "model": "recorded",
        "status": status,
        "created_at": "2026-10-02T00:00:00Z",
        "finished_at": None,
        "next_attempt_at": None,
        "last_heartbeat_at": None,
        "last_progress_at": None,
        "terminal_error": None,
        "final_answer": None,
        "workspace_version_id": str(uuid4()),
        "operations": [],
        **extra,
    }


def test_submit_reconnect_and_wait():
    requests = []
    submission_key = uuid4()

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body == {"prompt": "Write a document", "submission_key": str(submission_key)}
            summary = detail()
            for key in ("final_answer", "workspace_version_id", "operations"):
                summary.pop(key)
            return httpx.Response(200, json=summary)
        return httpx.Response(200, json=detail("succeeded", final_answer="Done"))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime/", http_client=http) as client:
                run = await client.runs.start(
                    prompt="Write a document", submission_key=submission_key
                )
                restored = await client.runs.get(run.id)
                assert restored.id == RUN_ID
                assert await restored.result() == "Done"
            assert not http.is_closed

    asyncio.run(scenario())
    assert [r.url.path for r in requests] == ["/api/runs", *[f"/api/runs/{RUN_ID}"] * 2]


def test_result_waits_and_surfaces_persisted_failure():
    states = iter(
        [
            detail(),
            detail("running"),
            detail(
                "failed",
                terminal_error={
                    "code": "attempts_exhausted",
                    "message": "Sandbox kept crashing",
                    "retryable": False,
                },
            ),
        ]
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=next(states)))
        ) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                with pytest.raises(RunFailed, match="Sandbox kept crashing") as failure:
                    await run.result(poll_interval=0.001)
                assert failure.value.run_id == RUN_ID
                assert failure.value.error.code == "attempts_exhausted"

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_only_stop_local_wait(cancel):
    requests = []
    entered = asyncio.Event()

    async def respond(request):
        requests.append(request)
        if len(requests) > 1:
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=detail())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                run = await client.runs.get(RUN_ID)
                if cancel:
                    task = asyncio.create_task(run.result())
                    await entered.wait()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(TimeoutError):
                        await run.result(timeout=0.01)

    asyncio.run(scenario())
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize(
    "body", [{"error": {"code": "missing", "message": "No run"}}, {"detail": []}]
)
def test_api_errors_are_not_retried(body):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(404, json=body)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            async with Client("http://runtime", http_client=http) as client:
                with pytest.raises(ApiError) as failure:
                    await client.runs.get(RUN_ID)
                assert failure.value.status_code == 404
                assert failure.value.details == body

    asyncio.run(scenario())
    assert len(requests) == 1


def test_owned_http_client_is_closed():
    async def scenario():
        async with Client("http://runtime") as client:
            http = client._http
        assert http.is_closed

    asyncio.run(scenario())


def test_client_import_does_not_load_services():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from durable_agent.client import Client; import sys; "
            "assert not any(name in sys.modules for name in "
            "['fastapi', 'sqlalchemy', 'openai', 'durable_agent.control'])",
        ],
        check=True,
    )
