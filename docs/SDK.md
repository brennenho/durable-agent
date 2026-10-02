# Python SDK

Define an agent and its tools in your own Python module. Deploy that module with the
runtime and submit custom agent runs through the async client. The existing UI shows
their progress, results, and files; its submission form starts the built-in agent.

## Start with the example

[`examples/sdk_agent/app.py`](../examples/sdk_agent/app.py) writes a Markdown document
with one custom tool. It needs no model key. The
[client script](../examples/sdk_agent/client.py) submits a run or reconnects by ID.

An application explicitly registers its decorated classes and functions:

```python
from durable_agent import AgentApp

app = AgentApp(agents=[DocumentWriter], tools=[save_document])
```

Use `@agent(name="document_writer", version="1")` on the agent class and
`@tool(name="save_document", version="1")` on the async tool. Names and versions are
stable execution identities. Keep decorated tools at module scope so the sandbox
can import them. Type annotations define tool argument schemas; docstrings describe them.

## Agent and tool boundaries

| API | Responsibility |
| --- | --- |
| `start(ctx: AgentContext)` | Choose the first step for this input. |
| `advance(ctx: AgentContext, result: StepResult)` | Choose what happens after an accepted result. |
| `Next(state=..., step=...)` | Save JSON state and schedule a `ModelCall`, `ToolCall`, or `ChildBatch`. |
| `Done(output=...)` | Finish with a JSON result; retain the last state. Supply `state=` to replace it. |
| `Fail(code=..., message=...)` | Finish with an explicit failure. |

Callbacks are synchronous and perform no I/O. Read input, saved state, history, and
limits from `ctx`; return a decision. Mutating a context object does not save progress.
Control commits accepted progress, the returned state, and the next operation together.

Tools perform the I/O. An injected `ctx: ToolContext` exposes the attempt's private
`workspace` and `remaining_seconds`. For example:

```python
@tool(name="save_document", version="1")
async def save_document(ctx: ToolContext, text: str):
    if not text:
        return ToolError(code="empty_document", message="Supply document text")
    await ctx.workspace.write_text("document.md", text)
    return {"path": "document.md"}
```

Import `tool`, `ToolContext`, and `ToolError` from `durable_agent`. Return `ToolError`
for a business error; the agent receives it in `result.error`. Unexpected tool-code
errors are execution failures. The runtime owns deadlines and retry policy.

## Submit and reconnect

```python
from uuid import uuid4
from durable_agent import Client

submission_key = uuid4()  # Store this before submitting; reuse it after a lost response.
async with Client("http://localhost:8100") as client:
    run = await client.runs.start(
        agent="document_writer",
        input={"title": "Release notes", "body": "The SDK is running."},
        submission_key=submission_key,
    )
    run_id = run.id
    output = await run.result(timeout=60)
```

With the example deployed, run its client from the repository root:

```bash
PYTHONPATH=examples uv run --project backend python -m sdk_agent.client --url http://localhost:8100
```

The script prints the submission key and run ID. Use `--submission-key UUID` to
repeat a submission or `--run-id UUID` to reconnect.

A timeout ends only the caller's wait. Closing the client does not stop the run.
Reconnect with `run = await client.runs.get(run_id)` and wait again.

- `run.status()`, `history()`, `children()`, and `inspect(operation_id)` read progress.
- `run.files.list()` returns a saved workspace version; `read(path, version_id=...)` returns bytes.
- `run.pause()` blocks new claims; an in-progress call may finish. `resume()` continues saved work.
- `run.follow_up(input=..., submission_key=...)` continues after this exact run.
- `run.fork(after_operation_id=..., input=..., submission_key=...)` starts an independent branch.

Follow-ups require a terminal predecessor. Forks require an accepted checkpoint.
Changed input under the same submission key conflicts. Handle `ApiError` for HTTP
errors, `RunFailed` for terminal execution failure, and `TimeoutError` for a caller deadline.
Transport errors remain `httpx` exceptions; the client adds no hidden retries.

## Deployment and recovery

Requires a local Docker daemon, its CLI, and `uv`. From the repository root:

```bash
export COMPOSE_PROJECT_NAME=durable-agent-sdk-example
export CONTROL_PORT=8100 TOOLS_PORT=8101 FRONTEND_PORT=3100 POSTGRES_PORT=5433
export SANDBOX_WORKSPACE_ROOT="/tmp/${COMPOSE_PROJECT_NAME}-workspaces"
export AGENT_BUILD_ID=sdk-example-v1
export OPENAI_MODEL=unused OPENAI_API_KEY=unused
SDK_DOCKER_ENDPOINT=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
export DOCKER_SOCKET="${SDK_DOCKER_ENDPOINT#unix://}"
mkdir -p "$SANDBOX_WORKSPACE_ROOT"
docker build -f docker/Dockerfile.sdk-example --target sandbox \
  --build-arg AGENT_BUILD_ID="$AGENT_BUILD_ID" -t durable-agent-sdk-sandbox:dev .
export SANDBOX_IMAGE_DIGEST=$(docker image inspect durable-agent-sdk-sandbox:dev --format '{{.Id}}')
docker compose -f compose.yaml -f compose.sdk-example.yaml up --build -d --wait
```

The placeholder OpenAI settings satisfy the base service configuration; this example
makes no model calls. These ports and project name keep it separate from the default
development stack. Choose unused values if they are already occupied. The endpoint
must be a local `unix://` socket. Open the UI at `http://localhost:3100`.

The overlay sets `AGENT_APP=sdk_agent.app:app`. Both images contain the app and the
same `AGENT_BUILD_ID`. For your own app, install its module and dependencies in Control
and the sandbox, and configure both startup variables. Change the build identifier
when code changes; keep matching code available for unfinished runs. Each run pins
its sandbox image. Use this overlay rather than `dev-up.sh`, which builds the default
sandbox without the example module.

The runtime enforces step, attempt, and time limits. Agent code sees `ctx.limits`;
model/tool steps can request `timeout_seconds`. Long commands should be split into
separate calls when intermediate progress needs to survive a crash.

Completed tool results and workspace versions are reused. An interrupted tool starts
again from its saved input workspace; uncommitted file edits are discarded. Only files
survive sandbox replacement, not processes or in-memory objects. External writes such
as email or remote database updates are unsupported by this recovery contract.
