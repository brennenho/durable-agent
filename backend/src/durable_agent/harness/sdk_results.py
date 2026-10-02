"""Convert accepted wire results into the SDK callback contract."""

from uuid import UUID

from durable_agent.contracts import ModelResult, OperationResult
from durable_agent.sdk import StepError, StepResult


def to_step_result(
    operation_id: UUID,
    result: OperationResult,
    workspace_version_id: UUID,
    *,
    tool_name: str | None = None,
) -> StepResult:
    if isinstance(result, ModelResult):
        payload = result.model_dump(mode="json")
        return StepResult(
            operation_id=operation_id,
            kind="model",
            value={**payload["output"], "native_context": payload["native_context"]},
            workspace_version_id=workspace_version_id,
        )
    output = result.output
    return StepResult(
        operation_id=operation_id,
        kind="children" if tool_name == "delegate" else "tool",
        value=output.model_dump(mode="json")["value"] if output.status == "ok" else None,
        error=StepError(code=output.code, message=output.message)
        if output.status == "error"
        else None,
        workspace_version_id=result.workspace_version_id,
    )
