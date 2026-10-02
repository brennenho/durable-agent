"""Shared wire payloads. Service behavior and persistence belong to their owners."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_as_utc)]
JsonObject = dict[str, JsonValue]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ExecutionError(Contract):
    code: str
    message: str
    retryable: bool
    retry_after_seconds: Annotated[float, Field(ge=0)] | None = None


class ErrorResponse(Contract):
    error: ExecutionError


class SandboxConfig(Contract):
    image_digest: str
    workspace_max_bytes: PositiveInt
    log_max_bytes: PositiveInt


class AgentConfig(Contract):
    model: str
    model_params: JsonObject
    tool_schemas: list[JsonObject]
    initial_workspace_version_id: UUID
    sandbox: SandboxConfig
    max_attempts: PositiveInt
    max_turns: PositiveInt
    call_timeout_seconds: PositiveInt


class ToolCall(Contract):
    call_id: str
    name: str
    arguments: JsonObject


class ToolSuccess(Contract):
    status: Literal["ok"]
    value: JsonObject


class ToolError(Contract):
    status: Literal["error"]
    code: str
    message: str


ToolOutput = Annotated[ToolSuccess | ToolError, Field(discriminator="status")]


class ModelOutput(Contract):
    status: Literal["completed", "incomplete"]
    text: str
    tool_calls: list[ToolCall]


class ModelResult(Contract):
    kind: Literal["model"]
    output: ModelOutput
    native_context: list[JsonObject]


class ToolResult(Contract):
    kind: Literal["tool"]
    call_id: str
    output: ToolOutput
    workspace_version_id: UUID


OperationResult = Annotated[ModelResult | ToolResult, Field(discriminator="kind")]


class PreviousTurn(Contract):
    prompt: str
    history: list[OperationResult]
    terminal_error: ExecutionError | None = None


class ModelRequest(Contract):
    prompt: str
    history: list[OperationResult]
    previous_turns: list[PreviousTurn] = Field(default_factory=list)


class ToolRequest(Contract):
    call: ToolCall
    input_version_id: UUID


class ModelOperation(Contract):
    kind: Literal["model"]
    request: ModelRequest


class ToolOperation(Contract):
    kind: Literal["tool"]
    request: ToolRequest


Operation = Annotated[ModelOperation | ToolOperation, Field(discriminator="kind")]


class SubmitRunRequest(Contract):
    submission_key: UUID
    prompt: str


class SubmitFollowUpRequest(SubmitRunRequest):
    after_run_id: UUID


class SubmitForkRequest(SubmitRunRequest):
    after_operation_id: UUID


RunStatus = Literal["queued", "running", "retrying", "succeeded", "failed"]
AttemptStatus = Literal["running", "accepted", "error", "unknown"]


class RunSummary(Contract):
    id: UUID
    prompt: str
    model: str
    status: RunStatus
    pause_requested: bool = False
    created_at: UtcDatetime
    finished_at: UtcDatetime | None
    next_attempt_at: UtcDatetime | None
    last_heartbeat_at: UtcDatetime | None
    last_progress_at: UtcDatetime | None
    terminal_error: ExecutionError | None


class AttemptView(Contract):
    id: UUID
    attempt_no: PositiveInt
    status: AttemptStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    error: ExecutionError | None
    provider_request_id: str | None = None
    usage: JsonObject | None = None


class BranchCheckpoint(Contract):
    workspace_version_id: UUID


class OperationView(Contract):
    id: UUID
    position: NonNegativeInt
    kind: Literal["model", "tool"]
    created_at: UtcDatetime
    completed_at: UtcDatetime | None
    result: OperationResult | None
    attempts: list[AttemptView]
    checkpoint: BranchCheckpoint | None = None
    tool_name: str | None = None
    tool_target: str | None = None


class OperationInspection(Contract):
    operation: Operation
    config: AgentConfig


class RunDetail(RunSummary):
    final_answer: str | None
    workspace_version_id: UUID
    operations: list[OperationView]
    max_attempts: PositiveInt | None = None


class ConversationSummary(Contract):
    id: UUID
    title: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    status: RunStatus
    pause_requested: bool = False
    latest_run_id: UUID


class ForkOrigin(Contract):
    conversation_id: UUID
    run_id: UUID
    operation_id: UUID
    position: NonNegativeInt


class ConversationDetail(ConversationSummary):
    runs: list[RunDetail]
    forked_from: ForkOrigin | None = None


class FileEntry(Contract):
    path: str
    kind: Literal["file", "directory"]
    size_bytes: NonNegativeInt | None = None


class FileListing(Contract):
    entries: list[FileEntry]


class RunFileListing(FileListing):
    workspace_version_id: UUID


class WorkspaceVersion(Contract):
    workspace_version_id: UUID


class ClaimRequest(Contract):
    worker_id: UUID


class Assignment(Contract):
    run_id: UUID
    operation_id: UUID
    attempt_id: UUID
    attempt_no: PositiveInt
    lease_generation: PositiveInt
    lease_expires_at: UtcDatetime
    deadline_at: UtcDatetime
    server_time: UtcDatetime
    config: AgentConfig
    operation: Operation


class HeartbeatRequest(Contract):
    worker_id: UUID
    lease_generation: PositiveInt


class HeartbeatResponse(Contract):
    lease_expires_at: UtcDatetime
    deadline_at: UtcDatetime
    server_time: UtcDatetime


class SuccessfulOutcome(Contract):
    status: Literal["succeeded"]
    result: OperationResult
    provider_request_id: str | None = None
    usage: JsonObject | None = None


class FailedOutcome(Contract):
    status: Literal["failed"]
    error: ExecutionError
    provider_request_id: str | None = None


AttemptOutcome = Annotated[SuccessfulOutcome | FailedOutcome, Field(discriminator="status")]


class ReportRequest(HeartbeatRequest):
    outcome: AttemptOutcome


class ReportResponse(Contract):
    status: Literal["recorded", "already_recorded"]


class ToolExecuteRequest(Contract):
    operation_id: UUID
    input_version_id: UUID
    name: str
    arguments: JsonObject
    lease_generation: PositiveInt
    deadline_at: UtcDatetime
    sandbox: SandboxConfig


class ToolExecuteResponse(Contract):
    output: ToolOutput
    workspace_version_id: UUID


class ToolCheckpoint(Contract):
    completed_at: UtcDatetime
    workspace_version_id: UUID


# Tool calls stay generic above so the harness can classify invalid calls as
# execution errors. The harness and tools use these types to validate arguments.
class ListFilesArguments(Contract):
    path: str = "."


class ReadFileArguments(Contract):
    path: str


class WriteFileArguments(Contract):
    path: str
    content: str


class RunCommandArguments(Contract):
    """Run a command. Split long workflows into separate calls to save progress between steps."""

    command: str
    cwd: str = "."
    timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=900,
        strict=True,
        description=(
            "Maximum command runtime in seconds. On timeout, unsaved file changes are discarded. "
            "Choose a longer budget or split the work into smaller commands."
        ),
    )


class ReadFileOutput(Contract):
    content: str


class WriteFileOutput(Contract):
    bytes_written: NonNegativeInt


class RunCommandOutput(Contract):
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool
