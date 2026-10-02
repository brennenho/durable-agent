"""Wire payloads for the independent, local Crash Test controller."""

from typing import Literal
from uuid import UUID

from durable_agent.contracts import Contract, JsonObject, RunDetail, ToolCheckpoint
from pydantic import Field

from crash_test.docker import Container


class Scenario(Contract):
    name: str
    hook: str | None
    description: str


class Observation(Contract):
    id: str
    at: str
    kind: str
    label: str
    operation_id: str | None = None
    detail: str | None = None
    hook: JsonObject | None = None
    target: Container | None = None


class Session(Contract):
    id: str
    scenario: str
    status: Literal["running", "passed", "failed", "inconclusive"]
    run: RunDetail | None
    observed_at: str | None
    error: str | None
    checkpoints: dict[str, ToolCheckpoint]
    checks: dict[str, bool]
    events: list[Observation]


class Snapshot(Contract):
    project: str
    services: list[Container]
    services_at: str | None
    services_error: str | None
    current: Session | None
    scenarios: list[Scenario]
    controls: list[Observation]
    autoscaling_enabled: bool
    autoscaling_checked_at: str | None


class AutoscalingSetting(Contract):
    enabled: bool


class StartScenario(Contract):
    scenario: str = Field(max_length=80)
    run_id: UUID | None = None


class ContainerAction(Contract):
    id: str = Field(min_length=1, max_length=128)
    started_at: str = Field(max_length=80)
    action: Literal["kill", "start"]
