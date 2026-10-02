# Crash Test hooks

Crash Test is the name for the fault-injection dashboard and scenario controller.
The optional hook client is independent of application persistence: no new database,
no event table, and no hook code in the pure harness.

A service uses `async with open_hooks("tools") as hooks` for its process lifetime,
then calls `await hooks.hit("tools.after_commit", operation_id=operation_id)` at an
execution boundary. With `CRASH_TEST_HOOK_URL` unset this performs no network I/O.
The client posts to that exact URL when enabled. Enable it on the existing development Compose project. Optional `CRASH_TEST_HOOK_TOKEN` sends a bearer token;
`CRASH_TEST_HOOK_TIMEOUT_SECONDS` sets a positive wait up to 30 seconds (default 10).

The controller receives a `HookEvent`: unique event ID, name, service, process hostname
(`instance`), PID, observation timestamp, and available run/operation/attempt/worker IDs,
lease generation, sandbox container ID, and workspace version ID. No prompt, tool
arguments, result body, or credentials are included. Hostname identifies a Compose
container when using Docker's default hostname; PID identifies the process inside it.
Resolve and verify targets within the controller's own Compose project before acting.

The response is exactly `{"action":"continue"}` or `{"action":"interrupt"}`.
The latter raises `HookInterrupted` in the current action. It is not a process kill;
service adapters must translate it to their ordinary retryable failure or abandonment.
To test a real process crash, hold the HTTP response while the controller kills the
identified service or sandbox, then record whether that target actually exited.
No service exposes an HTTP kill endpoint or accepts arbitrary commands in a hook reply.

Hooks must run outside database transactions. Waits consume the existing action
budget, never extend a lease/deadline, and propagate cancellation. Timeout, transport
failure, non-success HTTP, or invalid reply logs a warning and allows work to continue.
There is no automatic notification retry. A controller must mark missing acknowledgement
or an unobserved kill as inconclusive, not a successful fault injection.

For scenarios, arm a rule before submitting work: match the hook name plus operation ID
(or run ID where supplied), optionally generation, and consume the rule once before
acting. Unmatched hooks immediately continue. A delayed kill must recheck its target
and event against the still-armed rule; a client may have timed out or been cancelled.
Rules, fault actions, and observations belong to the controller's disposable state/log;
PostgreSQL remains authoritative for saved work and Control acceptance.

## Tools and sandbox boundaries

| Hook | Boundary | Context |
| --- | --- | --- |
| `sandbox.started` | Docker has started the named container; the command may already be running or finished. | operation ID, generation, sandbox container name |
| `tools.before_publish` | Execution and workspace capture finished; the publication transaction has not started. | operation ID, generation |
| `tools.after_commit` | The publication transaction committed and closed; the response has not been returned. | operation ID, generation, saved workspace ID |
| `tools.result_reused` | A saved completion was loaded on a matching retry; execution was skipped. | operation ID, generation, saved workspace ID |

Tools returns a retryable `503 crash_test_interrupted` when instructed to interrupt.
Before publication, no completion is saved. After commit, the saved result survives;
a matching retry can return it. Neither hook changes Control acceptance. Hook events
are observations, not a durable audit log, and can be missed when a process dies.
Sandbox interruption removes the container and private workspace before returning;
a real service kill relies on the existing container deadlines and startup cleanup.
With hooks disabled, the runner retains its normal attached execution path.

## Worker and Control boundaries

| Hook | Boundary | Interruption behavior |
| --- | --- | --- |
| `worker.after_claim` | Lease supervision started, before model or tool execution. | Report a retryable execution error. |
| `worker.before_report` | Execution returned, before the first report delivery. | Abandon delivery; lease expiry allows recovery. |
| `control.after_accept_commit` | A new successful report was accepted and its transaction closed, before acknowledgement. | Return retryable 503; replaying the report returns `already_recorded` without advancing twice. |

These events include run, operation, attempt, worker IDs and lease generation.
Worker heartbeats remain active during a held hook, within the original fixed deadline.
The report hook also consumes the existing delivery budget. Control does not emit its
acceptance hook for duplicate reports or recorded execution failures.

For the existing Compose project, set `CRASH_TEST_HOOK_URL` to the controller's URL
reachable from the containers and use `-f compose.yaml -f compose.crash-test.yaml`
with the existing startup configuration. The overlay opts Control, Tools, and Worker
into hooks; ordinary Compose startup remains unchanged. Keep the controller outside
the processes being killed. It can also kill frontend or Postgres in response to any
chosen hook, retaining observations while those services are unavailable.

The dashboard, rule API, scenario runner, and process-kill controller are separate
consumers of this interface; they are not implemented by the hook client.

## Container control

`crash_testing.docker.DockerControl` targets the selected existing Compose project.
The controller must bind it to the repository directory: both Compose working-directory
and config-file labels must match, in addition to the project and allowed service.
This works with existing containers without adding labels or creating another stack.
Automated tests may instead use explicitly labelled `crash-test-*` projects.
Sandboxes are identified by Tools' existing owner label matching that project.
Inventory excludes other projects. Every action revalidates ownership and the
container start time, rejecting a target that stopped or restarted after selection.
Killing uses SIGKILL with manual-stop semantics, verifies exit code 137, and leaves
restart explicit. Sandbox containers cannot be restarted; Tools must restore the
saved input into a replacement. Docker calls have bounded output and deadlines.
