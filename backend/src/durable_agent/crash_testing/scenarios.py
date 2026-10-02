"""Load scenario presets and verify recovery from observed and saved state."""

import tomllib
from datetime import datetime
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from durable_agent.contracts import RunDetail
from durable_agent.crash_testing.controller import FaultController


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    hook: Literal[
        "none",
        "tools.after_commit",
        "tools.before_publish",
        "worker.before_report",
        "control.after_accept_commit",
        "sandbox.started",
    ]
    description: str = Field(min_length=1)


def parse_scenarios(text: str) -> dict[str, tuple[str | None, str]]:
    definitions = TypeAdapter(
        dict[
            Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=80)],
            ScenarioDefinition,
        ]
    ).validate_python(tomllib.loads(text))
    if not definitions:
        raise ValueError("Scenario catalog must contain at least one scenario")
    return {
        name: (None if definition.hook == "none" else definition.hook, definition.description)
        for name, definition in definitions.items()
    }


SCENARIOS = parse_scenarios(
    files(__package__).joinpath("scenarios.toml").read_text(encoding="utf-8")
)


def evaluate(run: RunDetail, controller: FaultController) -> dict[str, bool]:
    checks = {
        "Run completed successfully": run.status == "succeeded",
        "Each operation has exactly one accepted attempt": bool(run.operations)
        and all(sum(a.status == "accepted" for a in op.attempts) == 1 for op in run.operations),
    }
    if controller.hook:
        checks["Requested fault was observed within the hook window"] = (
            controller.killed and controller.error is None
        )
    trigger = controller.trigger
    target = next((op for op in run.operations if trigger and op.id == trigger.operation_id), None)
    if not controller.hook:
        return checks
    checks["The faulted operation is present in the saved run"] = target is not None
    if target is None:
        return checks
    if controller.hook == "tools.after_commit":
        result = target.result
        events = [e for e in controller.journal.events if e.get("operation_id") == str(target.id)]
        checks["The original saved workspace was accepted"] = bool(
            result
            and result.kind == "tool"
            and result.workspace_version_id == trigger.workspace_version_id
        )
        events = [e for e in events if datetime.fromisoformat(e["at"]) >= trigger.observed_at]
        checks["Tools returned its saved result on retry"] = any(
            e["kind"] == "hook"
            and e["label"] == "tools.result_reused"
            and e["hook"]["workspace_version_id"] == str(trigger.workspace_version_id)
            for e in events
        )
        checks["No sandbox execution observed after saving"] = not any(
            e["label"] == "sandbox.started" for e in events
        )
    if controller.hook in {"tools.before_publish", "worker.before_report", "sandbox.started"}:
        claim = next(
            (
                e["hook"]
                for e in controller.journal.events
                if e["label"] == "worker.after_claim"
                and e.get("operation_id") == str(target.id)
                and e["hook"]["lease_generation"] == trigger.lease_generation
            ),
            None,
        )
        interrupted = next(
            (a for a in target.attempts if claim and str(a.id) == claim["attempt_id"]), None
        )
        checks["A later attempt recovered the interrupted operation"] = bool(
            interrupted
            and any(
                a.status == "accepted" and a.attempt_no > interrupted.attempt_no
                for a in target.attempts
            )
        )
    if controller.hook == "control.after_accept_commit":
        checks["The acknowledged operation kept its original accepted attempt"] = any(
            a.id == trigger.attempt_id and a.status == "accepted" for a in target.attempts
        )
    return checks
