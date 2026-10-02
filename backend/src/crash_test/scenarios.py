"""Load scenario presets and verify recovery from observed and saved state."""

import tomllib
from datetime import datetime
from importlib.resources import files
from typing import Annotated, Literal
from uuid import UUID

from durable_agent.contracts import ExecutionError, OperationView, RunDetail
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from crash_test.controller import COORDINATION_HOOKS, FaultController


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    hook: Literal[
        "none",
        "tools.after_commit",
        "tools.before_publish",
        "worker.before_report",
        "control.after_accept_commit",
        "control.after_children_commit",
        "control.after_join_commit",
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


def _child_report_matches(result: dict, child: RunDetail) -> bool:
    if child.status == "succeeded" and child.final_answer is not None:
        return (
            result.get("report") == child.final_answer[:16_384]
            and result.get("error") is None
            and result.get("truncated") is (len(child.final_answer) > 16_384)
        )
    try:
        error = ExecutionError.model_validate(result.get("error"))
    except ValidationError:
        return False
    return (
        child.status == "failed"
        and child.terminal_error is not None
        and result.get("report") is None
        and error == child.terminal_error
        and result.get("truncated") is False
    )


def _delegation_saved(
    run: RunDetail, operation: OperationView, saved_children: dict[UUID, RunDetail]
) -> bool:
    expected = [child for child in run.children if child.parent_operation_id == operation.id]
    result = operation.result
    if result is None or result.kind != "tool" or result.output.status != "ok":
        return False
    children = result.output.value.get("children")
    return (
        operation.completed_at is not None
        and not operation.attempts
        and 1 <= len(expected) <= 3
        and [child.child_index for child in expected] == list(range(len(expected)))
        and isinstance(children, list)
        and all(isinstance(child, dict) for child in children)
        and [child.get("run_id") for child in children] == [str(child.id) for child in expected]
        and [child.get("status") for child in children] == [child.status for child in expected]
        and [child.get("name") for child in children] == [child.name for child in expected]
        and all(
            child.id in saved_children and _child_report_matches(result, saved_children[child.id])
            for child, result in zip(expected, children, strict=True)
        )
    )


def evaluate(
    run: RunDetail, controller: FaultController, child_runs: list[RunDetail] | None = None
) -> dict[str, bool]:
    children = {child.id: child for child in child_runs or []}
    operations = [op for saved_run in [run, *children.values()] for op in saved_run.operations]
    delegated = [op for op in operations if op.tool_name == "delegate"]
    checks = {
        "Run completed successfully": run.status == "succeeded",
        "Worker operations were accepted exactly once": all(
            saved_run.operations for saved_run in [run, *children.values()]
        )
        and all(
            sum(a.status == "accepted" for a in op.attempts) == 1
            for op in operations
            if op.tool_name != "delegate"
        ),
    }
    if run.children or children or delegated:
        checks["All saved children completed successfully"] = bool(children) and all(
            child.status == "succeeded" for child in children.values()
        )
        checks["Child runs retained their saved parent links"] = (
            set(children) == {child.id for child in run.children}
            and len(children) == len(run.children)
            and all(
                children[child.id].parent is not None
                and children[child.id].parent.run_id == run.id
                and children[child.id].parent.operation_id == child.parent_operation_id
                and children[child.id].status == child.status
                for child in run.children
                if child.id in children
            )
        )
        checks["Delegation saved every child result without worker attempts"] = bool(
            delegated
        ) and all(_delegation_saved(run, operation, children) for operation in delegated)
    if controller.hook:
        checks["Requested fault was observed within the hook window"] = (
            controller.killed and controller.error is None
        )
    trigger = controller.trigger
    target = next((op for op in operations if trigger and op.id == trigger.operation_id), None)
    if not controller.hook:
        return checks
    checks["The faulted operation is present in the saved run"] = target is not None
    if target is None:
        return checks
    if controller.hook in COORDINATION_HOOKS:
        checks["The committed delegation retained the same children after recovery"] = (
            target.tool_name == "delegate"
            and controller.children_by_operation.get(target.id)
            == tuple(child.id for child in run.children if child.parent_operation_id == target.id)
            and _delegation_saved(run, target, children)
        )
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
