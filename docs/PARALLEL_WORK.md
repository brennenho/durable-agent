# Parallel implementation

Use one agent per component and one integration owner. Only folders, dependencies,
and basic service entry points are scaffolded. The deliverables below are future work.
Use the agreed payloads in `docs/API.md`. Integration implements the shared types and
route signatures first, then component owners build against the generated contracts.

| Owner | Files | Deliverable |
| --- | --- | --- |
| Frontend | `frontend/src/` except `lib/api.generated.ts`; `frontend/tests/` | Submit runs; poll status, output, and attempt/error timeline; saved file downloads. |
| Control | `backend/src/durable_agent/control/`; `backend/tests/control/` | Run APIs, state reconstruction, claims, leases, atomic acceptance, retries, recovery. |
| Harness | `backend/src/durable_agent/harness/`; `backend/tests/harness/` | Pure `start`/`advance`, tool validation/order, turn limits, final output. |
| Worker | `backend/src/durable_agent/worker/`; `backend/tests/worker/` | Claim loop, concurrent heartbeat, bounded calls, OpenAI adapter, report delivery. |
| Tools | `backend/src/durable_agent/tools/`; `backend/tests/tools/` | File/command tools, shared runner, workspace store, atomic completion. |
| Integration | Shared contracts, `db/`, migrations, `contracts/`, dependency files, `docker/`, Compose, `scripts/`, `backend/tests/integration/` | Schema and environment setup, contract changes, component wiring, durability tests. |

## Frontend

- Import generated API types and use `/api/*` through Next.js.
- Keep the initial UI to the scope in the DD. Handle loading, errors, and retries.
- Build against local test fixtures while control is unfinished; keep fixtures in tests.
- Complete when the UI handles a real run and its persisted attempt history.

## Control

- Own writes to `runs`, `operations`, and `attempts`.
- Use PostgreSQL transactions and run-row locking for claims, reports, and recovery.
- Reconstruct harness state and atomically persist the returned transition.
- Recover expired leases at startup and periodically; preserve retry budgets/deadlines.
- Attach the latest accepted workspace version when creating each tool operation;
  keep that input fixed across retries. Derive the current version from accepted results.
- Implement readiness once schema access and recovery startup are working.
- Complete when concurrent claims and duplicate/stale reports satisfy the DD invariants.

## Harness

- Implement the harness interface specified in `docs/API.md`; keep I/O and clocks out.
- Validate tool calls, preserve their ordered plan in the model result, and return only
  the next operation. Create later tool operations after earlier results are accepted.
- Treat native model context as opaque. Cover final responses, multiple tools, malformed
  output, incomplete output, and turn limits using recorded results.
- Coordinate any `AgentState` or transition changes with control before changing the interface.

## Worker

- Use HTTP for control/tools; do not import their repositories or access PostgreSQL.
- Keep heartbeats independent of slow model/tool calls. Respect the assignment's fixed deadline.
- Disable SDK retries. Preserve normalized output, native context, request IDs, and usage.
- Retry report delivery without rerunning the completed call. Stop starting calls after lease loss.
- Forward the stable operation key, input workspace version, generation, deadline,
  and frozen configuration. Return the tool's output version in its completion report.
- Complete when the worker executes both call types and survives dropped reports without
  sending a second external call for an already-recorded attempt.

## Tools

- Own writes to `tool_requests` and `workspace_versions`; never write runtime tables.
- Keep tool functions, shared runner, and workspace storage in modules under `tools/`.
- Compare immutable input under each operation key and return saved completions.
- Restore the saved input workspace into a private sandbox for each attempt. Capture
  the complete workspace and tool result together, checking the registered generation.
- Save normal command failures, including failing tests, as completed results with their
  resulting workspace. Crashes/timeouts discard uncommitted changes.
- Enforce isolation, deadlines, archive validation, size limits, and container cleanup.
- Implement readiness after tool tables are available.
- Complete when interrupted writes publish nothing, committed files survive replacement,
  retries return the same result/version, and stale attempts cannot publish.

## Integration

- Define shared models and migration setup when database implementation begins.
- Implement the agreed payloads in `contracts.py` and generate OpenAPI/TypeScript
  before component owners depend on them. Error categories are fixed in `docs/API.md`.
- Keep queries in the owning component, with explicit transaction boundaries.
- Keep one migration chain. Component owners supply schema needs; this owner writes revisions.
- Maintain `contracts.py`, API route signatures, and shared harness signatures in coordination
  with their component owners. Regenerate OpenAPI and TypeScript together.
- Manage dependency/lockfile changes and Compose readiness/startup wiring.
- Wire the read-only startup workspace import and the initial-version endpoint. Control
  freezes that version per run. Use the fixed Python/pytest sandbox image from the API spec.
- Wire the trusted sandbox launcher without exposing its Docker socket, credentials,
  or host directories to sandbox code. The current Compose file does not do this yet.
- Run the DD failure-injection suite against PostgreSQL and actual service processes.
- Remove scaffold notices/tests as real behavior replaces them; update README status.

## Integration order

1. Implement the agreed contracts and schema while components develop against fixtures.
2. Connect control to the harness; connect the worker to control and tools.
3. Complete one model → tool → model run and display it in the UI.
4. Run concurrent work and failure-injection tests; fix recovery before adding features.

For separate worktrees, commit the scaffold as a shared baseline before creating
the worktrees. Merge shared contracts/schema first, then component branches.
For a shared checkout, keep edits within the assigned paths. Component owners should
not modify generated files or regenerate shared lockfiles independently.
