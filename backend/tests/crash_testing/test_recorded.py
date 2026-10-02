import asyncio
from uuid import uuid4

from durable_agent.contracts import ModelRequest, ToolResult
from durable_agent.crash_testing.recorded import MODEL, PROMPT, RecordedModel

from ..worker.conftest import config as config


def test_recorded_responses_repeat_for_the_same_saved_input(config):
    config.model = MODEL

    async def exercise():
        model, request = RecordedModel(), ModelRequest(prompt=PROMPT, history=[])
        first = await model.execute(config, request, timeout_seconds=1)
        assert first == await model.execute(config, request, timeout_seconds=1)
        assert first.result.output.tool_calls[0].name == "run_command"
        assert first.result.output.tool_calls[0].call_id == "call_marker"
        accepted = ToolResult(
            kind="tool",
            call_id="call_marker",
            workspace_version_id=uuid4(),
            output={"status": "ok", "value": {"exit_code": 0}},
        )
        request.history = [first.result, accepted]
        final = await model.execute(config, request, timeout_seconds=1)
        assert final.status == "succeeded" and not final.result.output.tool_calls
        assert final == await model.execute(config, request, timeout_seconds=1)
        request.history = []
        assert first == await model.execute(config, request, timeout_seconds=1)
        request.prompt = "An unrelated application prompt"
        rejected = await model.execute(config, request, timeout_seconds=1)
        assert rejected.status == "failed" and not rejected.error.retryable
        request.prompt, request.history = PROMPT, [first.result, accepted]
        accepted.output.value["exit_code"] = 1
        assert (await model.execute(config, request, timeout_seconds=1)).status == "failed"

    asyncio.run(exercise())
