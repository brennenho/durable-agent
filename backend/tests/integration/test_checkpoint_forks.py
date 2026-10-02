"""Branch a saved round through real Control, Worker, Tools, and PostgreSQL."""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from durable_agent.control.runtime import Settings

from .test_conversation_flow import answer, call, response, running_services


async def execute_steps(services, worker, run_id, count):
    assignments = []
    for _ in range(count):
        claimed_at = asyncio.get_running_loop().time()
        assignment = await services.claim(worker.worker_id)
        assert assignment is not None
        assert str(assignment.run_id) == run_id
        assignments.append(assignment)
        await worker.run_assignment(assignment, claimed_at=claimed_at)
    return assignments


def test_fork_restores_only_selected_context_and_files_after_lost_response(
    postgres_engine, monkeypatch
):
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    for name in ("WORKSPACE_INPUT_DIR", "SANDBOX_WORKSPACE_ROOT", "SANDBOX_IMAGE_DIGEST"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        database_url=database_url,
        tools_url="http://tools",
        model="fixture",
        image_digest="sha256:" + "a" * 64,
    )
    read_call = {
        "type": "function_call",
        "id": "fc_branch_read",
        "call_id": "branch_read",
        "name": "read_file",
        "arguments": json.dumps({"path": "report.md"}),
        "status": "completed",
    }
    plan = [
        response([call("write_a", "A\n")], "a"),
        response([call("write_b", "B\n")], "b"),
        response(answer("Source finished at B"), "source_final"),
        response([read_call], "branch_read"),
        response([call("write_c", "C\n")], "c"),
        response(answer("Branch finished at C"), "branch_final"),
    ]
    model_requests, lost_report_acks, lost_fork_acks = [], [], []

    def provider(request):
        model_requests.append(json.loads(request.content))
        return httpx.Response(200, json=deepcopy(plan.pop(0)))

    async def exercise():
        async with running_services(settings, provider, lost_report_acks) as (
            api,
            services,
            worker,
        ):
            created = await api.post(
                "/api/conversations",
                json={"submission_key": str(uuid4()), "prompt": "Write A then B"},
            )
            assert created.status_code == 200, created.text
            source_id = created.json()["id"]
            source_assignments = await execute_steps(services, worker, source_id, 5)
            source = (await api.get(f"/api/runs/{source_id}")).json()
            assert source["status"] == "succeeded", source
            selected = source["operations"][1]
            version_a = selected["checkpoint"]["workspace_version_id"]
            assert version_a == selected["result"]["workspace_version_id"]
            assert version_a != source["workspace_version_id"]
            fork_path = f"/api/runs/{source_id}/forks"
            fork_body = {
                "submission_key": str(uuid4()),
                "prompt": "Read the checkpoint, then write C",
                "after_operation_id": selected["id"],
            }

            async def lose_fork_response(request):
                result = await api.send(request)
                assert result.status_code == 200, result.text
                lost_fork_acks.append(result.json()["id"])
                raise httpx.ReadError("Fork committed; response lost", request=request)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lose_fork_response), base_url="http://control"
            ) as unreliable:
                with pytest.raises(httpx.ReadError, match="response lost"):
                    await unreliable.post(fork_path, json=fork_body)

        # The source and fork survive fresh service/worker instances and a new seed import.
        async with running_services(settings, provider, lost_report_acks) as (
            api,
            services,
            worker,
        ):
            replayed = await api.post(fork_path, json=fork_body)
            assert replayed.status_code == 200, replayed.text
            branch_id = replayed.json()["id"]
            assert lost_fork_acks == [branch_id]
            assert branch_id != source_id
            conversation = (await api.get(f"/api/conversations/{branch_id}")).json()
            assert conversation["forked_from"] == {
                "conversation_id": source_id,
                "run_id": source_id,
                "operation_id": selected["id"],
                "position": selected["position"],
            }
            assert len(conversation["runs"]) == 1
            branch = conversation["runs"][0]
            branch_run_id = branch["id"]
            assert branch["workspace_version_id"] == version_a
            assert len(branch["operations"]) == 1
            assert branch["operations"][0]["attempts"] == []
            branch_assignments = await execute_steps(services, worker, branch_run_id, 5)
            first = branch_assignments[0]
            assert str(first.config.initial_workspace_version_id) == version_a
            assert first.operation.request.history == []
            assert {a.operation_id for a in source_assignments}.isdisjoint(
                a.operation_id for a in branch_assignments
            )
            assert [
                a.operation.request.call.call_id
                for a in branch_assignments
                if a.operation.kind == "tool"
            ] == ["branch_read", "write_c"]
            branch = (await api.get(f"/api/runs/{branch_run_id}")).json()
            assert branch["status"] == "succeeded", branch
            assert (await api.get(f"/api/runs/{source_id}")).json() == source
            assert await services.claim(worker.worker_id) is None
            replayed = await api.post(fork_path, json=fork_body)
            assert replayed.status_code == 200
            assert replayed.json()["id"] == branch_id
            assert len((await api.get("/api/conversations")).json()) == 2
            assert await services.claim(worker.worker_id) is None
            for run_id, version, expected in [
                (source_id, version_a, b"A\n"),
                (source_id, source["workspace_version_id"], b"B\n"),
                (branch_run_id, version_a, b"A\n"),
                (branch_run_id, branch["workspace_version_id"], b"C\n"),
            ]:
                saved = await api.get(
                    f"/api/runs/{run_id}/file",
                    params={"version_id": version, "path": "report.md"},
                )
                assert saved.status_code == 200, saved.text
                assert saved.content == expected

        assert not plan
        assert len(model_requests) == 6
        assert len(lost_report_acks) == 1
        # The fork's first inference receives exactly the source prefix through A.
        assert model_requests[3]["input"] == [
            *model_requests[1]["input"],
            {"role": "user", "content": fork_body["prompt"]},
        ]
        read_result = next(
            item
            for item in model_requests[4]["input"]
            if item.get("type") == "function_call_output" and item["call_id"] == "branch_read"
        )
        assert json.loads(read_result["output"]) == {"status": "ok", "value": {"content": "A\n"}}

    asyncio.run(exercise())
