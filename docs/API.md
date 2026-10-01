# API contracts

These interfaces are defined in `contracts.py`, typed FastAPI routes, and
`harness/contracts.py`. OpenAPI and frontend TypeScript are generated from the routes.

IDs are UUIDs. Timestamps are UTC. Fields marked `?` are nullable. Reject unexpected
request fields. The browser sends only a submission key and prompt; control freezes
configuration for the run. No service sends API keys in these payloads.

Nullable run, operation, and attempt view fields are always present, using `null`
when unknown. Retry delays, provider request IDs, usage, and file sizes may be omitted.
Timestamps require a timezone and are normalized to UTC. Attempt numbers, lease
generations, and execution limits are positive; operation positions are nonnegative.

## Starting workspace and environment

- Tools import `WORKSPACE_INPUT_DIR` once at startup. Unset means an empty workspace;
  a configured path that is missing or invalid fails startup. Apply the DD's archive
  validation and size limit. Import the complete directory; do not silently omit files.
- The configured folder is mounted read-only into the trusted tool service. Sandbox
  attempts receive copies of its saved version, never the original host directory.
- Restart tools and control to import changed starting files. Existing runs retain
  their saved version. This version has no file-upload or Git-cloning UI.
- Use a separate sandbox image with Python 3.12, its standard library, pytest 9.1.1,
  and `/bin/sh`. Install dependencies at image build time, and freeze the resolved
  image digest per run. Sandbox commands have no network or credentials.
- The first supported files are Python, plain text, and Markdown. No runtime package
  installation, Node runtime, or Word/PDF tooling is required initially.

## Shared types

| Type | Fields |
| --- | --- |
| `ExecutionError` | `code: string`, `message: string`, `retryable: bool`, `retry_after_seconds?: number` |
| `SandboxConfig` | `image_digest: string`, `workspace_max_bytes: int`, `log_max_bytes: int` |
| `AgentConfig` | `model: string`, `model_params: object`, `tool_schemas: object[]`, `initial_workspace_version_id: uuid`, `sandbox: SandboxConfig`, `max_attempts: int`, `max_turns: int`, `call_timeout_seconds: int` |
| `ToolCall` | `call_id: string`, `name: string`, `arguments: object` |
| `ModelOutput` | `status: completed or incomplete`, `text: string`, `tool_calls: ToolCall[]` |
| `ModelResult` | `kind: model`, `output: ModelOutput`, `native_context: object[]` |
| `ToolResult` | `kind: tool`, `call_id: string`, `output: ToolOutput`, `workspace_version_id: uuid` |
| `OperationResult` | `ModelResult` or `ToolResult`, discriminated by `kind` |
| `ModelRequest` | `prompt: string`, `history: OperationResult[]` |
| `ToolRequest` | `call: ToolCall`, `input_version_id: uuid` |
| `Operation` | `{kind: model, request: ModelRequest}` or `{kind: tool, request: ToolRequest}` |

`native_context` contains the provider's response output items. The OpenAI adapter
uses them to reconstruct subsequent requests; the harness treats them as opaque.
Send only tool output to the model, not runtime workspace IDs. Run configuration
is immutable and travels with assignments; tool schemas come from the fixed registry.

`ToolOutput` is one of:

```json
{"status": "ok", "value": {}}
{"status": "error", "code": "not_found", "message": "File does not exist"}
```

Both are completed tool outputs. A command that exits nonzero uses `status: ok`
with its exit code and logs: the command ran, and the model can inspect the failure.

## Frontend → control

| Method | Path | Request → response |
| --- | --- | --- |
| POST | `/api/runs` | `{submission_key, prompt}` → `RunSummary` |
| GET | `/api/runs` | → `RunSummary[]`, newest first |
| GET | `/api/runs/{run_id}` | → `RunDetail` |
| GET | `/api/runs/{run_id}/files` | → `{workspace_version_id, entries: FileEntry[]}` |
| GET | `/api/runs/{run_id}/file?version_id=…&path=…` | → stored bytes for that version/path |

- `RunSummary`: `id`, `prompt`, `model`, `status`, `created_at`, `finished_at?`,
  `next_attempt_at?`, `last_heartbeat_at?`, `last_progress_at?`, `terminal_error?`.
  Status is queued, running, retrying, succeeded, or failed.
- `RunDetail`: summary plus `final_answer?`, `workspace_version_id`, and `operations[]`.
- Operation view: `id`, `position`, `kind`, `created_at`, `completed_at?`, `result?`,
  and `attempts[]`. Attempt view: `id`, `attempt_no`, `status`, `started_at`,
  `finished_at?`, `error?`. Return both lists in execution order.
- `FileEntry`: `path`, `kind` (file or directory), and `size_bytes?` for files.
  File listings cover the saved workspace recursively, sorted by path.

Submission returns `200` for a new run or matching resubmission; a changed prompt
under the same key returns `409`. Reuse the original frozen configuration on a retry.
Unknown runs/files return `404`. File downloads require the version returned by the
listing; control validates that it is the seed or an accepted version for this run.
Control proxies file reads to tools. Next.js proxies only public `/api/*` routes.

## Conversations

A conversation groups ordered runs. Each run is one user prompt and its execution.
Existing run/file URLs remain valid; an initial run and its conversation share an ID.

| Method | Path | Request → response |
| --- | --- | --- |
| POST | `/api/conversations` | `SubmitRunRequest` → `ConversationSummary` |
| GET | `/api/conversations` | → `ConversationSummary[]`, newest updated first |
| GET | `/api/conversations/{conversation_id}` | → `ConversationDetail` |

`ConversationSummary`: `id`, `title`, `created_at`, `updated_at`, `status`, `latest_run_id`.
The title is the first prompt normalized to one line and limited to 80 characters.
Status and updated time come from the latest run's saved progress. `ConversationDetail`
adds `runs: RunDetail[]`, oldest first. Creation submits the first prompt; matching
submission keys reuse the original conversation. Follow-up submission is not yet exposed.

## Worker → control

| Method | Path | Request → response |
| --- | --- | --- |
| POST | `/internal/claims` | `{worker_id}` → `Assignment` or JSON `null` when idle |
| POST | `/internal/attempts/{attempt_id}/heartbeat` | `{worker_id, lease_generation}` → `{lease_expires_at, deadline_at, server_time}` |
| POST | `/internal/attempts/{attempt_id}/report` | `{worker_id, lease_generation, outcome}` → `{status: recorded or already_recorded}` |

`Assignment` contains `run_id`, `operation_id`, `attempt_id`, `attempt_no`,
`lease_generation`, `lease_expires_at`, `deadline_at`, `server_time`, `config`,
and `operation: Operation`. The claim commits before returning. An idle response
never means the database was unavailable; outages return `503`.

`outcome` is one of:

```text
{status: succeeded, result: OperationResult, provider_request_id?: string, usage?: object}
{status: failed, error: ExecutionError, provider_request_id?: string}
```

Heartbeats and new reports must match the current worker and lease generation.
Reject expired or superseded attempts with `409`; the worker abandons that attempt.
Retry report delivery with the same body, without repeating execution. Identical
recorded reports return `already_recorded`; changed reports conflict. Only a successful
operation result advances the harness. A recorded execution error invokes the retry policy.

Workers use `server_time` and `deadline_at` to maintain a monotonic local time budget.
PostgreSQL time remains authoritative. SDKs and tool functions do not add hidden retries.

## Worker → tools

`POST /tools/execute`:

```text
request: {
  operation_id, input_version_id, name, arguments,
  lease_generation, deadline_at, sandbox: SandboxConfig
}
response: {output: ToolOutput, workspace_version_id}
```

The runner compares name, arguments, input version, and sandbox configuration for
matching operation IDs. Generation and deadline belong to an attempt and may change.
Return `200` only after the output and workspace version are saved together. A repeated
completed request returns the same response. Changed input or a stale generation
returns `409`. Overlapping matching calls may compute separately, but only the current
registered generation may commit, and a completed result cannot be replaced.

| Tool | Arguments | Successful output value |
| --- | --- | --- |
| `list_files` | `{path: "."}`; path optional | `{entries: FileEntry[]}` for immediate children, sorted by path |
| `read_file` | `{path}` | `{content: string}`; UTF-8, up to 64 KiB |
| `write_file` | `{path, content}` | `{bytes_written: int}`; replace UTF-8 content, creating parent directories |
| `run_command` | `{command, cwd: "."}`; cwd optional | `{exit_code: int, stdout: string, stderr: string, truncated: bool}` |

Paths are relative to `/workspace`. `run_command` uses `/bin/sh -c`; its working
directory is explicit and does not persist between calls. Bound combined stdout/stderr
by the frozen log limit, marking truncation. Read-only calls return the input version.
Text reads over the limit return a `too_large` tool error instead of incomplete content.
The worker adds the saved model `call_id` when constructing `ToolResult`; it never uses
that provider ID as the operation key.

Additional tool-service reads:

| Method | Path | Response |
| --- | --- | --- |
| GET | `/tools/workspace/initial` | `{workspace_version_id}`; available after the startup import |
| GET | `/tools/workspace-versions/{version_id}/files` | `{entries: FileEntry[]}`; recursive listing |
| GET | `/tools/workspace-versions/{version_id}/file?path=…` | bytes with content type and safe download filename |

Control reads the initial version before creating a new run, then stores it in run
configuration. It returns an existing run on resubmission before consulting current
startup settings. Initial imports and checkpoint reads remain tool-owned operations.

## Error handling

| Failure | Action |
| --- | --- |
| Missing file, invalid workspace path, oversized text read, command exits nonzero | Save a completed tool result and workspace; return it to the model. No automatic retry. |
| Sandbox crash, timeout, transient transport/database/provider failure | Report a retryable execution error. Restore the operation's fixed input on retry. |
| Invalid model/tool-call structure, unknown tool, invalid credentials/configuration, snapshot exceeds limits | Permanent execution error; fail the run. |
| Expired or superseded lease | Stop the attempt. Control owns recovery. |

Application errors use non-2xx responses with `{error: ExecutionError}`. FastAPI
request validation uses its standard `422` response with `detail`; health endpoints
are separate. A lost connection without a response is also retryable.
Model-facing errors are inside a `200` completed response. Failed checkpoint capture
never returns an unsaved tool result as completed. Apply the DD's fixed deadlines and
attempt budget; control owns backoff and provider retry delays.

## Control → harness

`start(config, prompt)` and `advance(state, result)` are pure functions. State contains
`config`, `prompt`, `history`, `pending_tool_calls`, and `turn_count`, reconstructed from
saved rows. `history` excludes the new result passed to `advance`.

A transition is one of:

```text
{status: continue, next: {kind: model, request: ModelRequest}}
{status: continue, next: {kind: tool, call: ToolCall}}
{status: finished, final_answer: string}
{status: failed, error: ExecutionError}
```

Save the complete model tool plan but return only one next operation. Control assigns
its ID and position. For tool operations, control attaches the latest accepted workspace
version before persisting the immutable request. Harness failures are terminal;
transport failures never enter `advance`.

## First integration check

Run an edit that commits, crash the sandbox during the next call, and verify that a
replacement sandbox restores the edit and repeats only the interrupted operation.
Use recorded model outputs for this test. The DD lists the remaining failure cases.
