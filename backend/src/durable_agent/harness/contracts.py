"""Pure harness inputs and transitions; the control service supplies persistence."""

from typing import Annotated, Literal

from pydantic import Field

from durable_agent.contracts import (
    AgentConfig,
    Contract,
    ExecutionError,
    ModelOperation,
    NonNegativeInt,
    OperationResult,
    ToolCall,
)


class AgentState(Contract):
    config: AgentConfig
    prompt: str
    history: list[OperationResult]
    pending_tool_calls: list[ToolCall]
    turn_count: NonNegativeInt


class PlannedToolOperation(Contract):
    kind: Literal["tool"]
    call: ToolCall


NextOperation = Annotated[ModelOperation | PlannedToolOperation, Field(discriminator="kind")]


class ContinueTransition(Contract):
    status: Literal["continue"]
    next: NextOperation


class FinishedTransition(Contract):
    status: Literal["finished"]
    final_answer: str


class FailedTransition(Contract):
    status: Literal["failed"]
    error: ExecutionError


Transition = Annotated[
    ContinueTransition | FinishedTransition | FailedTransition, Field(discriminator="status")
]
