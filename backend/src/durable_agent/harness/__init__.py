"""Deterministic agent transitions. No network or database I/O."""

from pydantic import ValidationError

from durable_agent.contracts import (
    AgentConfig,
    ExecutionError,
    ListFilesArguments,
    ModelOperation,
    ModelRequest,
    ModelResult,
    OperationResult,
    ReadFileArguments,
    RunCommandArguments,
    WriteFileArguments,
)
from durable_agent.harness.contracts import (
    AgentState,
    ContinueTransition,
    FailedTransition,
    FinishedTransition,
    PlannedToolOperation,
    Transition,
)

ARGUMENTS = {
    "list_files": ListFilesArguments,
    "read_file": ReadFileArguments,
    "write_file": WriteFileArguments,
    "run_command": RunCommandArguments,
}


def fail(code: str, message: str) -> FailedTransition:
    return FailedTransition(
        status="failed", error=ExecutionError(code=code, message=message, retryable=False)
    )


def model_step(prompt: str, history: list[OperationResult]) -> ContinueTransition:
    return ContinueTransition(
        status="continue",
        next=ModelOperation(kind="model", request=ModelRequest(prompt=prompt, history=history)),
    )


def validate_config(config: AgentConfig) -> FailedTransition | None:
    names = [schema.get("name") for schema in config.tool_schemas]
    if any(not isinstance(name, str) or name not in ARGUMENTS for name in names):
        return fail("invalid_configuration", "Tool schemas must name supported tools")
    if len(names) != len(set(names)):
        return fail("invalid_configuration", "Tool schemas must have unique names")
    return None


def start(config: AgentConfig, prompt: str) -> Transition:
    if error := validate_config(config):
        return error
    return model_step(prompt, [])


def advance(state: AgentState, result: OperationResult) -> Transition:
    """turn_count counts accepted model results; pending includes the current tool."""
    if error := validate_config(state.config):
        return error
    if isinstance(result, ModelResult):
        if state.pending_tool_calls:
            return fail("invalid_result", "Expected a tool result before another model result")
        if state.turn_count >= state.config.max_turns:
            return fail("turn_limit", "Model turn limit reached")
        if result.output.status != "completed":
            return fail("incomplete_model_output", "Model output is incomplete")
        calls = result.output.tool_calls
        if not calls:
            return FinishedTransition(status="finished", final_answer=result.output.text)
        enabled = {schema.get("name") for schema in state.config.tool_schemas}
        seen = {
            call.call_id
            for previous in state.history
            if isinstance(previous, ModelResult)
            for call in previous.output.tool_calls
        }
        # Validate the entire plan before executing any of its effects.
        for call in calls:
            if not call.call_id or call.call_id in seen:
                return fail("invalid_tool_call", "Tool call IDs must be nonempty and unique")
            seen.add(call.call_id)
            arguments = ARGUMENTS.get(call.name)
            if arguments is None or call.name not in enabled:
                return fail("invalid_tool_call", f"Tool is not enabled: {call.name}")
            try:
                arguments.model_validate(call.arguments, strict=True)
            except ValidationError:
                return fail("invalid_tool_call", f"Invalid arguments for {call.name}")
        return ContinueTransition(
            status="continue", next=PlannedToolOperation(kind="tool", call=calls[0])
        )

    pending = state.pending_tool_calls
    if not pending or result.call_id != pending[0].call_id:
        return fail("invalid_result", "Tool result does not match the next pending call")
    if len(pending) > 1:
        return ContinueTransition(
            status="continue", next=PlannedToolOperation(kind="tool", call=pending[1])
        )
    if state.turn_count >= state.config.max_turns:
        return fail("turn_limit", "Model turn limit reached")
    return model_step(state.prompt, [*state.history, result])
