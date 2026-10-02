"""Replay accepted history; describe interruptions only in the new model input."""

import json

from durable_agent.contracts import (
    ExecutionError,
    ModelRequest,
    ModelResult,
    OperationResult,
    ToolCall,
    ToolError,
)


class InvalidModelInput(ValueError):
    pass


def response_input(request: ModelRequest) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for turn in request.previous_turns:
        items.extend(_turn_input(turn.prompt, turn.history, turn.terminal_error, seen))
    items.extend(_turn_input(request.prompt, request.history, None, seen))
    return items


def _turn_input(
    prompt: str, history: list[OperationResult], error: ExecutionError | None, seen: set[str]
) -> list[dict]:
    items = [{"role": "user", "content": prompt}]
    pending: list[str] = []
    for index, result in enumerate(history):
        if result.kind == "model":
            if pending:
                raise InvalidModelInput("Model response precedes outstanding tool results")
            try:
                pending = _call_ids(result, seen)
            except InvalidModelInput:
                # The harness rejects a whole plan before executing any of its calls.
                if error is None or index != len(history) - 1:
                    raise
                items.append(_interrupted_response(result))
                continue
            seen.update(pending)
            items.extend(result.native_context)
        else:
            if not pending or result.call_id != pending[0]:
                raise InvalidModelInput("Tool result does not match the next saved call")
            pending.pop(0)
            items.append(_tool_output(result.call_id, result.output.model_dump_json()))
    if error is None and pending:
        raise InvalidModelInput("Saved history has unanswered tool calls")
    if error is not None:
        output = ToolError(
            status="error",
            code="interrupted",
            message=f"No accepted result for this call. Turn failed: {error.code}: {error.message}",
        ).model_dump_json()
        items.extend(_tool_output(call_id, output) for call_id in pending)
        items.append(
            {
                "role": "assistant",
                "content": "Runtime status: the previous turn failed "
                f"({error.code}): {error.message}",
            }
        )
    return items


def _call_ids(result: ModelResult, seen: set[str]) -> list[str]:
    calls: list[ToolCall] = []
    try:
        if result.output.status != "completed":
            raise ValueError
        for item in result.native_context:
            if item["type"] == "function_call":
                if item.get("status") not in {None, "completed"}:
                    raise ValueError
                calls.append(
                    ToolCall(
                        call_id=item["call_id"],
                        name=item["name"],
                        arguments=json.loads(item["arguments"]),
                    )
                )
            elif item["type"] not in {"message", "reasoning"}:
                raise ValueError
        ids = [call.call_id for call in calls]
        if (
            calls != result.output.tool_calls
            or any(not call.call_id or not call.name for call in calls)
            or len(ids) != len(set(ids))
            or seen.intersection(ids)
        ):
            raise ValueError
        return ids
    except (KeyError, TypeError, ValueError):
        raise InvalidModelInput("Saved model output cannot be replayed as native context") from None


def _interrupted_response(result: ModelResult) -> dict:
    record = {
        "text": result.output.text,
        "tool_intent": [
            item for item in result.native_context if item.get("type") == "function_call"
        ],
    }
    return {
        "role": "assistant",
        "content": "Runtime record of interrupted model output, not a completed answer or tool "
        f"result:\n{json.dumps(record)}",
    }


def _tool_output(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}
