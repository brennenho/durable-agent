"""Recovery evidence uses operation/checkpoint identity, regardless of tool name."""

import asyncio
import json
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
import pytest
from crash_test.runner import ScenarioRun
from durable_agent.crash_test import HookEvent

from .test_children import family
from .test_runner import Docker, completed_run


@pytest.mark.parametrize("tool_name", ["run_command", "score_document"])
@pytest.mark.parametrize("in_child", [False, True])
@pytest.mark.parametrize(
    "evidence",
    ["saved", "wrong_checkpoint", "missing_reuse", "executed_again"],
)
def test_saved_tool_recovery_preserves_identity_and_rejects_bad_evidence(
    tmp_path, tool_name, in_child, evidence
):
    async def exercise():
        root, child = family() if in_child else (completed_run(), None)
        target_run = child or root
        target = target_run["operations"][1]
        target["tool_name"] = tool_name
        value = (
            {"exit_code": 0, "stdout": "Saved\n", "stderr": "", "truncated": False}
            if tool_name == "run_command"
            else {"score": 0.92, "labels": ["durable"], "metadata": {"reviewed": True}}
        )
        target["result"]["output"]["value"] = value
        saved_version = target["result"]["workspace_version_id"]
        first_attempt = target["attempts"][0]
        recovered_attempt = {
            **first_attempt,
            "id": str(uuid4()),
            "attempt_no": 2,
            "status": "accepted",
        }
        first_attempt["status"] = "unknown"
        target["attempts"].append(recovered_attempt)

        initial_root, initial_target = deepcopy(root), deepcopy(target_run)
        initial_target.update(status="running", finished_at=None, final_answer=None)
        initial_operation = initial_target["operations"][1]
        initial_operation.update(result=None, completed_at=None)
        initial_operation["attempts"] = [
            {**first_attempt, "status": "running", "finished_at": None}
        ]
        initial_target["operations"] = initial_target["operations"][:2]
        if in_child:
            initial_root.update(status="waiting", finished_at=None, final_answer=None)
            initial_root["children"][0].update(status="running", finished_at=None)
            initial_root["operations"][1].update(result=None, completed_at=None)
            initial_root["operations"] = initial_root["operations"][:2]
        else:
            initial_root = initial_target

        recovered = False
        checkpoint_reads = []

        def response(request):
            assert request.method == "GET"
            if request.url.path.endswith("/checkpoint"):
                checkpoint_reads.append(request.url.path)
                assert str(target["id"]) in request.url.path
                return httpx.Response(
                    200,
                    json={
                        "completed_at": target["completed_at"],
                        "workspace_version_id": saved_version,
                    },
                )
            if request.url.path == f"/api/runs/{root['id']}":
                return httpx.Response(200, json=root if recovered else initial_root)
            assert in_child and request.url.path == f"/api/runs/{child['id']}"
            return httpx.Response(200, json=child if recovered else initial_target)

        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(response)
        ) as client:
            docker = Docker()
            scenario = ScenarioRun(
                "saved-response-lost", docker, client, tmp_path, run_id=UUID(root["id"])
            )
            task = asyncio.create_task(scenario.execute(timeout=3, poll=0.001))
            try:
                await scenario.ready.wait()
                claim = HookEvent(
                    name="worker.after_claim",
                    service="worker",
                    instance="worker",
                    pid=1,
                    run_id=target_run["id"],
                    operation_id=target["id"],
                    attempt_id=first_attempt["id"],
                    lease_generation=1,
                )
                await scenario.observe_hook(claim)
                committed = HookEvent(
                    name="tools.after_commit",
                    service="tools",
                    instance="tools",
                    pid=1,
                    operation_id=target["id"],
                    lease_generation=1,
                    workspace_version_id=saved_version,
                )
                await scenario.observe_hook(committed)
                assert scenario.controller.killed and docker.containers["tools"].running

                await scenario.observe_hook(
                    claim.model_copy(
                        update={
                            "event_id": uuid4(),
                            "attempt_id": UUID(recovered_attempt["id"]),
                            "lease_generation": 2,
                        }
                    )
                )
                if evidence != "missing_reuse":
                    await scenario.observe_hook(
                        committed.model_copy(
                            update={
                                "event_id": uuid4(),
                                "name": "tools.result_reused",
                                "lease_generation": 2,
                            }
                        )
                    )
                if evidence == "executed_again":
                    await scenario.observe_hook(
                        committed.model_copy(
                            update={"event_id": uuid4(), "name": "sandbox.started"}
                        )
                    )
                if evidence == "wrong_checkpoint":
                    target["result"]["workspace_version_id"] = str(uuid4())
                recovered = True
                await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            assert scenario.status == ("passed" if evidence == "saved" else "failed")
            report = json.loads(scenario.journal.path.with_suffix(".json").read_text())
            assert report == scenario.snapshot()
            assert report["checkpoints"][target["id"]]["workspace_version_id"] == saved_version
            saved_run = report["child_runs"][0] if in_child else report["run"]
            saved_operation = saved_run["operations"][1]
            assert saved_run["id"] == target_run["id"]
            assert saved_operation["id"] == target["id"]
            assert saved_operation["result"]["output"]["value"] == value
            assert saved_operation["attempts"][1]["id"] == recovered_attempt["id"]
            assert checkpoint_reads
            assert sum(event["kind"] == "kill_observed" for event in report["events"]) == 1

    asyncio.run(exercise())
