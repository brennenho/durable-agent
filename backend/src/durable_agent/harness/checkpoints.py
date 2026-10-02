"""Identify valid closed rounds in an accepted history without changing its state."""

from uuid import UUID

from durable_agent.harness import advance
from durable_agent.harness.contracts import AgentState


def closed_rounds(state: AgentState) -> dict[int, UUID]:
    """Map accepted-history indices to the workspace saved at each closed round."""
    replay = state.model_copy(update={"history": [], "pending_tool_calls": [], "turn_count": 0})
    version = state.config.initial_workspace_version_id
    checkpoints = {}
    for index, result in enumerate(state.history):
        transition = advance(replay, result)
        # The last tool still closes its round when no model turns remain.
        if transition.status == "failed" and not (
            result.kind == "tool" and transition.error.code == "turn_limit"
        ):
            break
        if result.kind == "model":
            replay.turn_count += 1
            replay.pending_tool_calls = list(result.output.tool_calls)
        else:
            replay.pending_tool_calls.pop(0)
            version = result.workspace_version_id
        replay.history.append(result)
        if not replay.pending_tool_calls:
            checkpoints[index] = version
        if transition.status != "continue":
            break
    return checkpoints
