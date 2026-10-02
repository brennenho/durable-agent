"""Serializable decisions and context for pure agent callbacks."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_serializer

JsonObject = dict[str, JsonValue]
Name = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$", max_length=80)]
Seconds = Annotated[int, Field(ge=1, le=900, strict=True)]


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Limits(Value):
    max_steps: int = Field(default=100, ge=1, strict=True)
    max_attempts: int = Field(default=5, ge=1, strict=True)
    call_timeout_seconds: Seconds = 120


class ModelCall(Value):
    kind: Literal["model"] = "model"
    prompt: str = ""
    context: list[JsonObject] = Field(default_factory=list)
    tools: list[Name] = Field(default_factory=list)
    model: str | None = None
    parameters: JsonObject = Field(default_factory=dict)
    timeout_seconds: Seconds = 120


class ToolCall(Value):
    kind: Literal["tool"] = "tool"
    name: Name
    arguments: JsonObject = Field(default_factory=dict)
    timeout_seconds: Seconds = 120


class ChildTask(Value):
    name: Name
    agent: Name
    input: JsonValue


class ChildBatch(Value):
    kind: Literal["children"] = "children"
    tasks: Annotated[list[ChildTask], Field(min_length=1, max_length=3)]


Step = Annotated[ModelCall | ToolCall | ChildBatch, Field(discriminator="kind")]


class StepError(Value):
    code: str
    message: str


class StepResult(Value):
    operation_id: UUID
    kind: Literal["model", "tool", "children"]
    value: JsonValue = None
    error: StepError | None = None
    workspace_version_id: UUID | None = None


class AgentContext(Value):
    run_id: UUID
    agent: Name
    version: str = Field(min_length=1)
    input: JsonValue
    state: JsonValue = None
    history: tuple[StepResult, ...] = ()
    workspace_version_id: UUID
    limits: Limits = Field(default_factory=Limits)


class Next(Value):
    status: Literal["next"] = "next"
    state: JsonValue
    step: Step


class Done(Value):
    status: Literal["done"] = "done"
    output: JsonValue
    # Omission retains the prior state; explicit None replaces it with JSON null.
    state: JsonValue = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if "state" not in self.model_fields_set:
            value.pop("state", None)
        return value


class Fail(Value):
    status: Literal["failed"] = "failed"
    code: str
    message: str


Decision = Annotated[Next | Done | Fail, Field(discriminator="status")]


class ToolDefinition(Value):
    name: Name
    version: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    parameters: JsonObject
    description: str = ""
    takes_context: bool = False


class AgentIdentity(Value):
    name: Name
    version: str = Field(min_length=1)
    app_ref: str = Field(min_length=1)
    build_id: str = Field(min_length=1)
