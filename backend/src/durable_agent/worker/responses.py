"""Translate provider output without executing calls or choosing retry delays."""

import json

from openai.types.responses import Response, ResponseFunctionToolCall

from durable_agent.contracts import (
    AttemptOutcome,
    ExecutionError,
    FailedOutcome,
    ModelOutput,
    ModelResult,
    SuccessfulOutcome,
    ToolCall,
)


def map_response(response: Response) -> AttemptOutcome:
    request_id = getattr(response, "_request_id", None)
    try:
        if error := response.error:
            return _failure(
                request_id,
                f"openai_{error.code}",
                error.message,
                retryable=error.code in {"server_error", "rate_limit_exceeded"},
            )
        if response.status not in {"completed", "incomplete"}:
            raise ValueError("Expected a terminal model response")
        output = _model_output(response)
        payload = response.model_dump(mode="json", exclude_unset=True)
        return SuccessfulOutcome(
            status="succeeded",
            result=ModelResult(
                kind="model",
                output=output,
                native_context=payload["output"],
            ),
            provider_request_id=request_id,
            usage=payload.get("usage"),
        )
    except (AttributeError, TypeError, ValueError):
        return _failure(
            request_id, "invalid_model_response", "Malformed or unsupported model output"
        )


def _model_output(response: Response) -> ModelOutput:
    text: list[str] = []
    calls: list[ToolCall] = []
    for item in response.output:
        if item.type == "message":
            for part in item.content:
                if part.type == "output_text":
                    text.append(part.text)
                elif part.type == "refusal":
                    text.append(part.refusal)
                else:
                    raise ValueError("Unsupported message content")
        elif item.type == "function_call":
            # Partial argument JSON is preserved in native context, never executed.
            if response.status == "completed":
                calls.append(_tool_call(item))
        elif item.type != "reasoning":
            raise ValueError("Unsupported output item")
    return ModelOutput(status=response.status, text="".join(text), tool_calls=calls)


def _tool_call(item: ResponseFunctionToolCall) -> ToolCall:
    if item.status not in {None, "completed"}:
        raise ValueError("Unfinished tool call in a completed response")
    return ToolCall(call_id=item.call_id, name=item.name, arguments=json.loads(item.arguments))


def _failure(request_id: str | None, code: str, message: str, *, retryable=False) -> FailedOutcome:
    return FailedOutcome(
        status="failed",
        error=ExecutionError(code=code, message=message, retryable=retryable),
        provider_request_id=request_id,
    )
