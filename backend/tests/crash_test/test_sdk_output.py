import asyncio
import json
from copy import deepcopy
from uuid import UUID

import httpx
import pytest
from crash_test.controller import FaultController, Journal
from crash_test.runner import ScenarioRun
from crash_test.scenarios import evaluate
from durable_agent.contracts import RunDetail
from durable_agent.crash_test import HookEvent

from .test_children import family
from .test_runner import Docker, completed_run

AGENT = {"name": "reviewer", "version": "1", "app_ref": "example:app", "build_id": "build-1"}
REPORT_CHECK = "Delegation saved every child result without worker attempts"


def sdk_family(output):
    root, child = family()
    root.update(agent=AGENT, final_answer=None, output={"done": True})
    child.update(agent=AGENT, final_answer=None, output=deepcopy(output))
    root["children"][0]["agent"] = AGENT
    report = root["operations"][1]["result"]["output"]["value"]["children"][0]
    report.pop("report")
    report.pop("truncated")
    report["output"] = deepcopy(output)
    return root, child, report


def report_check(root, child, tmp_path):
    controller = FaultController(
        Docker(), Journal(tmp_path / "events.jsonl"), UUID(root["id"]), None
    )
    return evaluate(RunDetail.model_validate(root), controller, [RunDetail.model_validate(child)])[
        REPORT_CHECK
    ]


@pytest.mark.parametrize("output", [None, False, 0, "", [], {}, {"scores": [0, 0.5], "ok": True}])
def test_sdk_child_report_matches_saved_output_and_requires_explicit_null(tmp_path, output):
    root, child, report = sdk_family(output)
    assert report_check(root, child, tmp_path)
    del report["output"]
    assert not report_check(root, child, tmp_path)
    report["output"] = {"different": output}
    assert not report_check(root, child, tmp_path)
    report["output"] = output
    report["error"] = {"code": "unexpected", "message": "Failure", "retryable": False}
    assert not report_check(root, child, tmp_path)


@pytest.mark.parametrize(
    "output,replacement", [(False, 0), ([1], [True]), ({"ok": True}, {"ok": 1})]
)
def test_sdk_report_comparison_preserves_json_types(tmp_path, output, replacement):
    root, child, report = sdk_family(output)
    report["output"] = replacement
    assert not report_check(root, child, tmp_path)


def test_sdk_report_compares_objects_independent_of_key_order(tmp_path):
    root, child, report = sdk_family({"a": 1, "b": {"x": 2, "y": 3}})
    report["output"] = {"b": {"y": 3, "x": 2}, "a": 1}
    assert report_check(root, child, tmp_path)


def test_failed_sdk_child_requires_the_saved_error_and_no_output(tmp_path):
    root, child, report = sdk_family(None)
    error = {"code": "agent_failed", "message": "Cannot review", "retryable": False}
    child.update(status="failed", terminal_error=error)
    root["children"][0].update(status="failed", terminal_error=error)
    report.update(status="failed", error=error)
    assert report_check(root, child, tmp_path)
    report["output"] = "invented success"
    assert not report_check(root, child, tmp_path)
    report.update(output=None, error={**error, "message": "Another error"})
    assert not report_check(root, child, tmp_path)
    report["error"] = error
    del report["output"]
    assert not report_check(root, child, tmp_path)


@pytest.mark.parametrize("preset", ["children-created", "children-joined"])
@pytest.mark.parametrize("output", [None, {"findings": [{"line": 3, "safe": False}]}])
def test_coordination_recovery_checks_sdk_output_for_immediate_children(tmp_path, preset, output):
    async def exercise():
        root, child, report = sdk_family(output)
        child["operations"] = []

        def response(request):
            assert not request.url.path.endswith("/checkpoint")
            return httpx.Response(200, json=child if child["id"] in request.url.path else root)

        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(response)
        ) as client:
            scenario = ScenarioRun(preset, Docker(), client, tmp_path, run_id=UUID(root["id"]))
            scenario.armed = True
            scenario.controller = FaultController(
                scenario.docker, scenario.journal, scenario.run_id, scenario.hook
            )
            await scenario.observe_hook(
                HookEvent(
                    name=scenario.hook,
                    service="control",
                    instance="control",
                    pid=1,
                    run_id=root["id"],
                    operation_id=root["operations"][1]["id"],
                )
            )
            assert scenario.controller.killed and scenario.docker.containers["control"].running
            await scenario.observe(scenario.run_id)
            checks = evaluate(scenario.run, scenario.controller, list(scenario.child_runs.values()))
            assert all(checks.values())
            snapshot = scenario.snapshot()
            assert snapshot["child_runs"][0]["output"] == output
            assert snapshot["child_runs"][0]["agent"] == AGENT
            report["output"] = {"wrong": True}
            await scenario.observe(scenario.run_id)
            checks = evaluate(scenario.run, scenario.controller, list(scenario.child_runs.values()))
            assert not checks[REPORT_CHECK]
            assert not checks["The committed delegation retained the same children after recovery"]

    asyncio.run(exercise())


@pytest.mark.parametrize("preset", ["baseline", "saved-response-lost"])
def test_immediate_sdk_completion_is_inspectable_but_not_a_crash_test_pass(tmp_path, preset):
    async def exercise():
        run = completed_run()
        run.update(agent=AGENT, final_answer=None, output=None, operations=[])
        async with httpx.AsyncClient(
            base_url="http://control",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=run)),
        ) as client:
            scenario = ScenarioRun(preset, Docker(), client, tmp_path, run_id=UUID(run["id"]))
            await scenario.execute(poll=0)
            assert scenario.status == "inconclusive"
            assert "no execution boundary" in scenario.error
            snapshot = scenario.snapshot()
            assert snapshot["run"]["status"] == "succeeded"
            assert snapshot["run"]["output"] is None
            assert snapshot["run"]["agent"] == AGENT
            assert not scenario.controller.killed
            assert json.loads(scenario.journal.path.with_suffix(".json").read_text()) == snapshot

    asyncio.run(exercise())
