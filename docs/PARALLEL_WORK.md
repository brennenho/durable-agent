# Parallel implementation

Use four implementation agents: **Runtime, Tools, Worker, and Frontend**. Keep the
original chat as the **Integration** owner. Each agent works in a separate worktree
and owns its branches. The human reviews and merges PRs.

Read `AGENTS.md`, `DESIGN.md`, and `docs/API.md` before starting. Repository docs are
the current specification; earlier conversation decisions may be outdated.
The repo has shared contract types and API/harness stubs. Database setup and component
behavior are still unimplemented. Component work starts after both foundation PRs merge.

## 1. Land the foundation first

Integration prepares two small PRs for human review:

1. **Contracts:** implement the agreed payloads in `contracts.py`, API route
   signatures, and harness state/transition signatures. Generate OpenAPI and
   TypeScript types. Unimplemented handlers must not return fake success.
2. **Database:** add shared SQLAlchemy models, session setup, and the initial
   Alembic migration with the DD's constraints. Verify migration on PostgreSQL.

After both PRs merge, Integration records the foundation commit in its handoff.
Start all four component branches from that commit. Do not create competing
contracts or migrations while waiting for the foundation.

```text
Foundation merged
  ├── Runtime: control + harness ─┐
  ├── Tools: files + sandbox ────┤
  ├── Worker: execution ─────────┼── Combined run → recovery tests
  └── Frontend: UI ──────────────┘
      Integration: shared changes, environment, and cross-service tests
```

## 2. Fork and assign ownership

The human starts one conversation fork per component. Before editing, each agent
confirms its worktree, foundation commit, assigned scope, and proposed PR stack.
Use Graphite as required by `AGENTS.md`. Independent components branch from trunk;
stack only changes that depend on earlier PRs in that component.

Source package paths (`control/`, `harness/`, `tools/`, `worker/`, `db/`, and
`contracts.py`) are under `backend/src/durable_agent/`. Other paths are repo-relative.

| Agent | Owned files | Responsibility |
| --- | --- | --- |
| Runtime | `control/`, `harness/`; `backend/tests/control/`, `backend/tests/harness/` | Run APIs, agent transitions, scheduling, leases, reports, recovery. |
| Tools | `tools/`; `backend/tests/tools/` | File tools, sandbox runner, workspace storage, tool completion. |
| Worker | `worker/`; `backend/tests/worker/` | Claims, heartbeats, OpenAI adapter, tool execution requests, reporting. |
| Frontend | `frontend/src/` except `frontend/src/lib/api.generated.ts`; `frontend/tests/` | Submission, status, final output, attempt/error history, saved files. |
| Integration | `contracts.py`, `db/`, migrations, generated files, dependencies/lockfiles, Docker/Compose, scripts, shared test setup, integration tests, repo docs and configuration | Shared foundation, environment, contract changes, system verification. |

Runtime owns both control and harness to keep their state transitions under one
owner. They remain separate modules. After the foundation handoff, Runtime and
Tools own their route implementations; wire contract changes go through Integration.

## 3. Build components concurrently

No component needs another component's finished implementation to start. Build
against shared types and fixtures at service boundaries. Keep fixtures in tests;
production paths must report unfinished or failed behavior honestly.

### Runtime

- Keep the harness pure: validate model results, preserve tool order, enforce turn
  limits, and return the next operation or final answer.
- Own queries and transactions for `runs`, `operations`, and `attempts`. Implement
  run/file APIs, claims, heartbeats, reports, retries, lease recovery, and readiness.
- Save accepted progress and the next operation atomically. Fix each tool operation's
  input workspace at creation; retain it on retry. Read tool workspaces over HTTP.
- **Develop against:** recorded model results, tool HTTP fixtures, and real PostgreSQL.
- **Ready when:** harness transitions pass; concurrent claims, duplicate/stale reports,
  and lease recovery preserve the DD's invariants.

### Tools

- Implement the four tools, initial workspace import, checkpoint reads, and readiness.
  Own queries and transactions for `tool_requests` and `workspace_versions` only.
- Restore the fixed input into a private sandbox. Save output and workspace together,
  checking the registered generation. Reuse saved completions on retry.
- Own runner code: isolation, deadlines, archive validation, limits, and cleanup.
  Integration owns the sandbox image and Docker wiring.
- **Develop against:** direct tool requests, real PostgreSQL, and the sandbox image.
- **Ready when:** crashes publish nothing; committed files survive replacement;
  duplicates reuse results; stale attempts cannot publish; normal command failures
  save their output and files.

### Worker

- Use HTTP for control/tools; never access PostgreSQL or import their repositories.
  Keep heartbeats independent of calls, enforce fixed deadlines, and abandon stale leases.
- Implement the OpenAI adapter without SDK retries. Preserve output, native context,
  request IDs, and usage. Forward operation IDs, versions, generations, and configuration.
- Retry report delivery without repeating the completed call.
- **Develop against:** HTTP fixtures and recorded OpenAI responses.
- **Ready when:** both call types execute, deadlines/lease loss stop attempts, and
  dropped report acknowledgments do not cause repeated execution.

### Frontend

- Use generated types and public `/api/*` routes through Next.js.
- Implement submission, polling, status/output, attempt/error history, and file downloads.
  Cover loading, empty, and error states.
- **Develop against:** API fixtures matching the generated types.
- **Ready when:** these flows pass component tests. Integration then verifies them
  against real runs and persisted history.

### Integration during parallel work

- Provide the fixed sandbox image, trusted launcher access, workspace mount, migrations,
  readiness ordering, and worker startup in Compose. Prioritize the sandbox environment
  so Tools can test actual execution early.
- Maintain shared contracts and schema; keep queries in the owning component.
  Prepare cross-service failure tests while components are being built.
- Run the shared Docker stack. Component tests needing containers must use separate
  Compose project names, ports, and databases; worktrees do not isolate those resources.

## 4. Coordinate changes and handoffs

- **Shared change needed:** post the proposed change, reason, and affected consumers in
  the component chat for the human to relay to Integration. Integration owns the shared
  PR, updates the docs, and identifies which agents must update. Relay the decision to
  affected chats; forks do not automatically share later discussion. Agents update
  only their own branches.
- **Another component is unfinished:** continue with contract fixtures. Do not implement
  its missing behavior in your component or edit its files.
- **PR ready:** self-review and run component checks, then provide PR links in review
  order, validation results, and remaining blockers. Keep unfinished PRs in draft.
- **Integration finds a defect:** return it to the component owner with a reproduction.
  That owner fixes its branch; Integration maintains the regression scenario.

Use recorded model responses for automated tests. Live OpenAI checks require an
explicit request. Never restack or submit another agent's branches. Human merges
remain the default.

## 5. Integrate and verify

Merge reviewed component PRs as they become ready; independent components do not
need to wait for each other. Integration verifies the combined code in this order:

1. Control calls the harness; the worker claims and reports model/tool operations.
2. One complete model → tool → model run appears in the UI with saved files.
3. A saved edit survives a crash in the next call; only the interrupted call repeats.
4. The remaining DD failure tests pass against PostgreSQL and actual service processes.

Component completion means its scope and tests pass. Project completion requires the
combined run and recovery tests. Integration updates README status as behavior lands.
