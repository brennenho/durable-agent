"""Deterministic agent transitions. No network or database I/O."""

from durable_agent.contracts import AgentConfig, OperationResult
from durable_agent.harness.contracts import AgentState, Transition


def start(config: AgentConfig, prompt: str) -> Transition:
    raise NotImplementedError("Harness start is not implemented")


def advance(state: AgentState, result: OperationResult) -> Transition:
    raise NotImplementedError("Harness advance is not implemented")
