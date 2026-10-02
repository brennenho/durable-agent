import time
from dataclasses import dataclass
from uuid import UUID

from durable_agent.sdk.workspace import Workspace


@dataclass(frozen=True)
class ToolContext:
    run_id: UUID
    operation_id: UUID
    attempt_id: UUID
    workspace: Workspace
    deadline: float

    @property
    def remaining_seconds(self) -> float:
        """Time remaining before the runtime stops this attempt."""
        return max(0.0, self.deadline - time.monotonic())
