"""An explicit recorded workload; retry selection depends on saved input, not call count."""

import json
from pathlib import Path

from openai.types.responses import Response

from durable_agent.contracts import AgentConfig, FailedOutcome, ModelRequest
from durable_agent.worker.responses import map_response

PROMPT = "Crash Test: write a unique execution marker to crash-proof.txt and return it."
MODEL = "crash-test-recorded"


class RecordedModel:
    def __init__(self):
        self.responses = json.loads((Path(__file__).parent / "fixtures/workload.json").read_text())

    async def execute(self, config: AgentConfig, request: ModelRequest, *, timeout_seconds: float):
        phase = None
        if config.model == MODEL and request.prompt == PROMPT and not request.previous_turns:
            if not request.history:
                phase = "plan"
            elif len(request.history) == 2:
                result = request.history[-1]
                if (
                    result.kind == "tool"
                    and result.call_id == "call_marker"
                    and result.output.status == "ok"
                    and result.output.value.get("exit_code") == 0
                ):
                    phase = "done"
        if phase is None:
            return FailedOutcome(
                status="failed",
                error={
                    "code": "recorded_workload_mismatch",
                    "message": "Input or tool result does not match the Crash Test fixture",
                    "retryable": False,
                },
            )
        return map_response(Response.model_validate(self.responses[phase]))
