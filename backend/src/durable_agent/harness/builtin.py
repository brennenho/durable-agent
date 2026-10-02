"""The built-in model/tool loop expressed through SDK callbacks."""

from durable_agent.contracts import AgentConfig, ModelResult, PreviousTurn, ToolResult
from durable_agent.harness import advance, start
from durable_agent.harness.contracts import AgentState, Transition
from durable_agent.harness.model_input import response_input
from durable_agent.sdk import AgentContext, Done, Fail, ModelCall, Next, StepResult, ToolCall


class BuiltinAgent:
    def __init__(self, config: AgentConfig):
        self.config = config

    def start(self, ctx: AgentContext):
        if not isinstance(ctx.input, str):
            return Fail(code="invalid_input", message="The built-in agent requires a text input")
        previous = []
        if ctx.state is not None:
            prior = self._restore(ctx)
            final = prior.history[-1] if prior.history else None
            complete = (
                isinstance(final, ModelResult)
                and final.output.status == "completed"
                and not final.output.tool_calls
            )
            previous = [
                *prior.previous_turns,
                {
                    "prompt": prior.prompt,
                    "history": prior.history,
                    "terminal_error": None
                    if complete
                    else {
                        "code": "interrupted",
                        "message": "Prior turn stopped before a final answer",
                        "retryable": False,
                    },
                },
            ]
        state = {
            "prompt": ctx.input,
            "previous_turns": [
                PreviousTurn.model_validate(turn).model_dump(mode="json") for turn in previous
            ],
            "history_offset": len(ctx.history),
        }
        return self._decision(start(self.config, ctx.input, previous_turns=previous), state)

    def advance(self, ctx: AgentContext, result: StepResult):
        state = self._restore(ctx)
        return self._decision(advance(state, self._result(result, state)), ctx.state)

    def _restore(self, ctx: AgentContext) -> AgentState:
        state = AgentState(
            config=self.config,
            prompt=ctx.state["prompt"],
            history=[],
            previous_turns=ctx.state["previous_turns"],
            pending_tool_calls=[],
            turn_count=0,
        )
        for result in ctx.history[ctx.state["history_offset"] :]:
            accepted = self._result(result, state)
            state.history.append(accepted)
            if accepted.kind == "model":
                state.turn_count += 1
                state.pending_tool_calls = list(accepted.output.tool_calls)
            else:
                state.pending_tool_calls = state.pending_tool_calls[1:]
        return state

    @staticmethod
    def _result(result: StepResult, state: AgentState):
        if result.kind == "model":
            value = dict(result.value)
            return ModelResult(
                kind="model", native_context=value.pop("native_context"), output=value
            )
        return ToolResult(
            kind="tool",
            call_id=state.pending_tool_calls[0].call_id,
            workspace_version_id=result.workspace_version_id,
            output={"status": "error", **result.error.model_dump()}
            if result.error
            else {"status": "ok", "value": result.value},
        )

    def _decision(self, transition: Transition, state: dict):
        if transition.status == "failed":
            return Fail(code=transition.error.code, message=transition.error.message)
        if transition.status == "finished":
            return Done(output=transition.final_answer, state=state)
        operation = transition.next
        if operation.kind == "tool":
            step = ToolCall(**operation.call.model_dump())
        else:
            step = ModelCall(
                context=response_input(operation.request),
                tools=[schema["name"] for schema in self.config.tool_schemas],
                parameters=self.config.model_params,
                timeout_seconds=self.config.call_timeout_seconds,
            )
        return Next(state=state, step=step)
