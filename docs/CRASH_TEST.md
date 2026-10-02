# Crash Test hooks

Crash Test is the name for the fault-injection dashboard and scenario controller.
The optional hook client is independent of application persistence: no new database,
no event table, and no hook code in the pure harness. Service wiring follows separately.

A service uses `async with open_hooks("tools") as hooks` for its process lifetime,
then calls `await hooks.hit("tools.after_commit", operation_id=operation_id)` at an
execution boundary. With `CRASH_TEST_HOOK_URL` unset this performs no network I/O.
The client posts to that exact URL when enabled. Configure it only in the disposable
Crash Test environment. Optional `CRASH_TEST_HOOK_TOKEN` sends a bearer token;
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
