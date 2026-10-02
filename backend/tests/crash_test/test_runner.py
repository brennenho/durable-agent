import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from crash_test.controller import now
from crash_test.docker import Container
from crash_test.runner import ScenarioRun


def completed_run():
    version, run_id = str(uuid4()), str(uuid4())
    operations = []
    for position, kind in enumerate(["model", "tool", "model"]):
        result = (
            {
                "kind": "tool",
                "call_id": "call_marker",
                "workspace_version_id": version,
                "output": {"status": "ok", "value": {"stdout": "marker\n", "exit_code": 0}},
            }
            if kind == "tool"
            else {
                "kind": "model",
                "output": {"status": "completed", "text": "", "tool_calls": []},
                "native_context": [],
            }
        )
        operations.append(
            {
                "id": str(uuid4()),
                "position": position,
                "kind": kind,
                "result": result,
                "created_at": now(),
                "completed_at": now(),
                "attempts": [
                    {
                        "id": str(uuid4()),
                        "attempt_no": 1,
                        "status": "accepted",
                        "started_at": now(),
                        "finished_at": now(),
                        "error": None,
                    }
                ],
            }
        )
    return {
        "id": run_id,
        "prompt": "Write a marker to crash-proof.txt.",
        "model": "test-model",
        "status": "succeeded",
        "created_at": now(),
        "finished_at": now(),
        "next_attempt_at": None,
        "last_heartbeat_at": None,
        "last_progress_at": now(),
        "terminal_error": None,
        "final_answer": "Saved",
        "workspace_version_id": version,
        "operations": operations,
    }


class Docker:
    def __init__(self):
        self.containers = {
            service: Container(service, service, service, service, True, now(), 0, "none")
            for service in ["control", "tools", "worker", "db", "frontend"]
        }

    async def inventory(self):
        return list(self.containers.values())

    async def inspect(self, key):
        return self.containers[key]

    async def kill(self, item):
        self.containers[item.id] = replace(item, running=False, exit_code=137)
        return self.containers[item.id]

    async def start(self, item):
        self.containers[item.id] = replace(item, running=True, started_at=now())
        return self.containers[item.id]


def test_runner_arms_for_next_normal_run_without_changing_workers(tmp_path):
    async def exercise():
        docker, run = Docker(), completed_run()
        from durable_agent.crash_test import HookEvent

        def response(request):
            assert request.method == "GET"
            if request.url.path.endswith("/checkpoint"):
                return httpx.Response(
                    200,
                    json={
                        "completed_at": now(),
                        "workspace_version_id": run["workspace_version_id"],
                    },
                )
            return httpx.Response(200, json=run)

        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(response)
        ) as client:
            scenario = ScenarioRun("baseline", docker, client, tmp_path)
            task = asyncio.create_task(scenario.execute(poll=0))
            while not scenario.armed:
                await asyncio.sleep(0)
            claim = HookEvent(
                name="worker.after_claim",
                service="worker",
                instance="worker",
                pid=1,
                run_id=run["id"],
                operation_id=run["operations"][0]["id"],
                lease_generation=1,
                attempt_id=run["operations"][0]["attempts"][0]["id"],
            )
            for invalid in [
                claim.model_copy(update={"observed_at": datetime.now(UTC) - timedelta(seconds=20)}),
                claim.model_copy(update={"instance": "foreign"}),
                claim.model_copy(update={"attempt_id": None}),
            ]:
                await scenario.observe_hook(invalid)
                assert scenario.controller is None
            await scenario.observe_hook(claim)
            await task
            assert scenario.status == "passed" and all(scenario.checks.values())
            assert all(c.running for c in docker.containers.values())
            assert (
                json.loads(scenario.journal.path.with_suffix(".json").read_text())
                == scenario.snapshot()
            )
            scenario = ScenarioRun(
                "saved-response-lost", docker, client, tmp_path, run_id=UUID(run["id"])
            )
            await scenario.execute(poll=0)
            assert scenario.status == "inconclusive"
            run["status"] = "failed"
            scenario = ScenarioRun("baseline", docker, client, tmp_path, run_id=UUID(run["id"]))
            await scenario.execute(poll=0)
            assert scenario.status == "failed"

    asyncio.run(exercise())


def test_outage_retains_state_and_deadline_ends_untriggered_rule(tmp_path):
    async def exercise():
        docker, run = Docker(), completed_run()
        offline = False

        def response(request):
            return httpx.Response(503) if offline else httpx.Response(200, json=run)

        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.MockTransport(response)
        ) as client:
            scenario = ScenarioRun("baseline", docker, client, tmp_path)
            run["operations"] = []
            await scenario.observe(UUID(run["id"]))
            observed = scenario.observed_at
            offline = True
            try:
                await scenario.observe(UUID(run["id"]))
            except httpx.HTTPError:
                pass
            assert scenario.run.id == UUID(run["id"]) and scenario.observed_at == observed
            await scenario.execute(timeout=0.02)
            assert scenario.status == "inconclusive" and not scenario.armed

    asyncio.run(exercise())


def test_verdict_requires_recovery_after_the_faulted_attempt(tmp_path):
    from crash_test.controller import FaultController, Journal
    from crash_test.scenarios import evaluate
    from durable_agent.contracts import RunDetail
    from durable_agent.crash_test import HookEvent

    run = RunDetail.model_validate(completed_run())
    target = run.operations[0]
    controller = FaultController(
        Docker(), Journal(tmp_path / "events.jsonl"), run.id, "worker.before_report"
    )
    trigger = HookEvent(
        name=controller.hook,
        service="worker",
        instance="worker",
        pid=1,
        operation_id=target.id,
        attempt_id=target.attempts[0].id,
        lease_generation=2,
    )
    controller.trigger, controller.killed = trigger, True
    target.attempts[0].attempt_no = 2
    target.attempts.insert(
        0,
        target.attempts[0].model_copy(update={"id": uuid4(), "attempt_no": 1, "status": "error"}),
    )
    controller.journal.events.append(
        {
            "label": "worker.after_claim",
            "operation_id": str(target.id),
            "hook": trigger.model_dump(mode="json"),
        }
    )
    assert not all(evaluate(run, controller).values())  # An earlier retry is not recovery.
    target.attempts[-1].status = "unknown"
    target.attempts.append(
        target.attempts[-1].model_copy(
            update={"id": uuid4(), "attempt_no": 3, "status": "accepted"}
        )
    )
    assert all(evaluate(run, controller).values())
    controller.hook = "tools.after_commit"
    controller.trigger = trigger.model_copy(update={"operation_id": uuid4()})
    assert not all(evaluate(run, controller).values())
