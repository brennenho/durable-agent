import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
from crash_test.controller import FaultController, Journal
from crash_test.runner import ScenarioRun
from crash_test.scenarios import evaluate
from durable_agent.contracts import RunDetail, RunSummary
from durable_agent.crash_test import HookEvent

from .test_runner import Docker, completed_run


def family():
    root, child = completed_run(), completed_run()
    delegation = root["operations"][1]
    delegation.update(tool_name="delegate", attempts=[])
    delegation["result"]["output"]["value"] = {
        "children": [
            {
                "run_id": child["id"],
                "name": "Inspect tests",
                "status": "succeeded",
                "report": child["final_answer"],
                "error": None,
                "truncated": False,
            }
        ]
    }
    root["children"] = [
        {
            **{k: v for k, v in child.items() if k in RunSummary.model_fields},
            "parent_operation_id": delegation["id"],
            "child_index": 0,
            "name": "Inspect tests",
        }
    ]
    child["parent"] = {
        "run_id": root["id"],
        "conversation_id": root["id"],
        "operation_id": delegation["id"],
        "name": "Inspect tests",
    }
    return root, child


def test_observe_family_skips_delegate_checkpoint_and_retains_children_through_outage(tmp_path):
    async def exercise():
        root, child = family()
        requested = []
        offline = False

        def response(request):
            requested.append(request.url.path)
            if offline:
                return httpx.Response(503)
            if request.url.path.endswith("/checkpoint"):
                return httpx.Response(200, content=b"null")
            return httpx.Response(200, json=child if child["id"] in request.url.path else root)

        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(response)
        ) as client:
            scenario = ScenarioRun("baseline", Docker(), client, tmp_path, run_id=UUID(root["id"]))
            await scenario.execute(poll=0)
            assert scenario.status == "passed"
            assert len(scenario.snapshot()["child_runs"]) == 1
            assert scenario.controller.run_ids == {UUID(root["id"]), UUID(child["id"])}
            assert requested.count(f"/api/runs/{child['id']}") == 1
            assert not any(root["operations"][1]["id"] in path for path in requested)
            assert any(child["operations"][1]["id"] in path for path in requested)
            before = scenario.snapshot()
            offline = True
            try:
                await scenario.observe(UUID(root["id"]))
            except httpx.HTTPError:
                pass
            assert scenario.snapshot() == before

    asyncio.run(exercise())


def test_delegation_verdict_requires_saved_child_identity_result_and_acceptance(tmp_path):
    root, child = family()
    controller = FaultController(
        Docker(), Journal(tmp_path / "events.jsonl"), UUID(root["id"]), None
    )

    def passes(root, child):
        return all(
            evaluate(
                RunDetail.model_validate(root), controller, [RunDetail.model_validate(child)]
            ).values()
        )

    assert passes(root, child)
    corruptions = []
    wrong_result = deepcopy(root)
    wrong_result["operations"][1]["result"]["output"]["value"]["children"][0]["run_id"] = str(
        uuid4()
    )
    corruptions.append((wrong_result, child))
    duplicate = deepcopy(root)
    duplicate["children"].append(duplicate["children"][0])
    corruptions.append((duplicate, child))
    wrong_parent = deepcopy(child)
    wrong_parent["parent"]["run_id"] = str(uuid4())
    corruptions.append((root, wrong_parent))
    missing_acceptance = deepcopy(child)
    missing_acceptance["operations"][0]["attempts"] = []
    corruptions.append((root, missing_acceptance))
    unfinished = deepcopy(root)
    unfinished["operations"][1]["result"] = None
    corruptions.append((unfinished, child))
    failed = deepcopy(child)
    failed["status"] = "failed"
    corruptions.append((root, failed))
    for bad_root, bad_child in corruptions:
        assert not passes(bad_root, bad_child)
    assert not all(evaluate(RunDetail.model_validate(root), controller).values())


def test_child_claim_before_poll_is_verified_against_parent_and_fault_recovery_checked(tmp_path):
    async def exercise():
        root, child = family()
        target = child["operations"][0]
        interrupted = {**target["attempts"][0], "id": str(uuid4()), "status": "unknown"}
        target["attempts"][0]["attempt_no"] = 2
        target["attempts"].insert(0, interrupted)
        async with httpx.AsyncClient(
            base_url="http://control",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=root)),
        ) as client:
            scenario = ScenarioRun(
                "worker-lost", Docker(), client, tmp_path, run_id=UUID(root["id"])
            )
            scenario.armed = True
            scenario.controller = FaultController(
                scenario.docker, scenario.journal, scenario.run_id, scenario.hook
            )
            claim = HookEvent(
                name="worker.after_claim",
                service="worker",
                instance="worker",
                pid=1,
                run_id=child["id"],
                operation_id=target["id"],
                attempt_id=interrupted["id"],
                lease_generation=1,
            )
            await scenario.observe_hook(
                claim.model_copy(update={"run_id": uuid4(), "event_id": uuid4()})
            )
            assert not scenario.controller.operations
            await scenario.observe_hook(claim)
            await scenario.observe_hook(
                claim.model_copy(update={"name": "worker.before_report", "event_id": uuid4()})
            )
            assert scenario.controller.killed
            checks = evaluate(
                RunDetail.model_validate(root),
                scenario.controller,
                [RunDetail.model_validate(child)],
            )
            assert all(checks.values())
            target["attempts"].pop()
            assert not all(
                evaluate(
                    RunDetail.model_validate(root),
                    scenario.controller,
                    [RunDetail.model_validate(child)],
                ).values()
            )

    asyncio.run(exercise())


def test_joined_reports_match_saved_answers_errors_and_truncation(tmp_path):
    root, child = family()
    child["final_answer"] = "x" * 16_384 + " tail"
    result = root["operations"][1]["result"]["output"]["value"]["children"][0]
    result.update(report="x" * 16_384, truncated=True)
    controller = FaultController(
        Docker(), Journal(tmp_path / "events.jsonl"), UUID(root["id"]), None
    )

    def report_saved():
        return evaluate(
            RunDetail.model_validate(root), controller, [RunDetail.model_validate(child)]
        )["Delegation saved every child result without worker attempts"]

    assert report_saved()
    for changes in [
        {"report": None},
        {"report": "corrupted"},
        {"report": "x"},
        {"truncated": False},
        {"error": {"message": "unexpected failure"}},
    ]:
        original = result.copy()
        result.update(changes)
        assert not report_saved()
        result.update(original)

    error = {"code": "turn_limit", "message": "Model turn limit reached", "retryable": False}
    child.update(status="failed", final_answer=None, terminal_error=error)
    root["children"][0].update(status="failed", terminal_error=error)
    result.update(status="failed", report=None, error=error, truncated=False)
    assert report_saved()
    result["error"] = {**error, "message": "Different failure"}
    assert not report_saved()
    result.update(error=error, report="Invented answer")
    assert not report_saved()
