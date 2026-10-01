# Working in this repository

Read `DESIGN.md`, `docs/API.md`, and your section in `docs/PARALLEL_WORK.md` first.

One human reviews this repository. Optimize for their ability to understand each
change, verify it, and review the stack in order.

## Scope and ownership

- Implement only the assigned task. The design describes future work; it does not
  authorize building it all. A scaffold request means structure and placeholders.
- Work in your assigned directories. Keep component tests in the matching test directory.
- The integration owner maintains shared contracts, generated API types, migrations,
  dependency manifests/lockfiles, Compose, and cross-service tests. Coordinate needed
  changes there instead of having several agents edit the same files.
- Use a separate worktree and owned branches for each agent's implementation work.
  Never switch branches in another agent's checkout or include their uncommitted changes.

## Clean code

- Prefer straightforward functions, explicit data flow, and names that explain intent.
  Keep modules focused and side effects visible at their boundaries.
- Add abstractions when they simplify the current requirement. Avoid speculative
  frameworks, generic repositories, plugin systems, and configuration for future needs.
- Follow existing conventions. Keep unrelated refactors, formatting, renames, and
  dependency upgrades out of a behavior change.
- Make errors explicit. Do not swallow exceptions, add hidden retries, or return fake
  success. Remove dead code and temporary debugging before requesting review.
- Comments explain invariants, tradeoffs, or surprising behavior. Keep prose short;
  avoid narrating obvious code or making unsupported claims about quality.
- Test observable behavior and meaningful failure cases. Avoid tests that merely
  repeat the implementation or mocks that hide the behavior being tested.

## Async I/O

- Use async database, HTTP, and subprocess APIs in services and workers. Do not block
  the event loop with synchronous clients, sleeps, or filesystem/archive work; offload
  blocking work to a thread or process when needed. Keep pure harness logic synchronous.
- Use one async database session per request or task; never share it across concurrent
  tasks. Use the same session type for reads and writes, with explicit commits for writes.
  Keep remote calls outside transactions. Services own their engine's startup/shutdown.
- Bound concurrent work, enforce deadlines, and propagate cancellation after cleanup.
  Calls within a run remain sequential. Migrations and standalone scripts may stay synchronous.

## Graphite and stacked PRs

- Use Graphite (`gt`) to create, update, restack, and submit PR branches. Use
  `gt create`, `gt modify`, `gt restack`, and `gt submit`; check installed command
  help for flags. See the [CLI reference](https://graphite.com/docs/command-reference).
  If Graphite is unavailable or unauthenticated, report the blocker; do not silently
  publish through a different workflow.
- Before coding, outline each proposed PR's behavior or invariant, validation, and
  parent dependency. Plan component work as a series of small PRs.
  Implement and submit the first complete change before building the rest of the component.
- Stack dependent changes on their parent PR. Start independent work from trunk.
  Keep stacks shallow; do not build a long chain on an unresolved design decision.
- Each PR must be correct and testable with its ancestors, without needing a later
  PR to repair it. Include the relevant tests with the behavior they verify.
- Shared contracts and schema changes belong to the integration owner. Depend on
  their PR when needed; do not duplicate that work in each component's stack.
- Stage only your files or hunks. Inspect the stack and command scope before changing
  history or submitting. Do not restack, overwrite, or submit another agent's branches.
  Coordinate when an ancestor change affects another owner's work.
- Address feedback in the PR that owns the change. Restack your dependent branches,
  inspect their diffs, and rerun affected checks. Keep each PR's base and description current.
- Keep unfinished work in draft. After self-review and relevant checks pass, mark PRs
  ready automatically with `gt submit --publish` so automatic review starts. Do not wait
  for permission or the rest of the stack. Scope the command to your completed PRs.
  Leave merging to the human; do not enable auto-merge or dismiss reviews on their behalf.

## Make review easy

- Aim for 150–300 reviewed lines per PR, counting additions and deletions against its
  immediate parent, including tests and migrations. Smaller is fine. Exclude generated
  API schemas/types and lockfile churn from that count, but identify them in the description.
- Above 400 reviewed lines, split before submitting unless doing so would break a
  correct, testable change. Explain that exception briefly in the PR description;
  the line target is a guide, not a reason to split tests from behavior or ship broken stages.
- Give each PR one review focus: a behavior, invariant, or concrete setup step.
  Keep its implementation, relevant tests, and required migration/generated updates together.
  Separate independently reviewable behavior and unrelated cleanup.
- A few stacked PRs are fine. Submit and share each complete PR as soon as it is ready;
  do not wait for the whole stack. Use judgment about stack depth and the human
  review pace, and address feedback before building further on disputed decisions.
- Check in during active work at least once a minute with useful progress, blockers,
  or scope changes. Share each PR link, validation, and next step when it is submitted.
- Review the diff against the immediate parent, including generated files. Remove
  accidental changes, stale comments, and unnecessary churn before submitting.
- Use a concrete title describing the resulting behavior. Describe the final change
  for someone who has not read the chat; omit the agent's work log.
- Keep PR descriptions short and cover:
  - **What and why:** the problem and resulting behavior; a before/after example when useful.
  - **Stack:** the parent dependency and what this PR adds. Identify deferred work explicitly.
  - **Validation:** exact checks run and their results, including anything not verified.
  - **Review notes:** where to start and the behavior or invariant to verify; identify
    material risks, generated files, and any necessary size exception.
- Do not ask the reviewer to infer correctness from a passing test count. Explain
  the invariant or failure case when the change affects durability or concurrency.
- After review feedback, briefly state what changed and which checks were rerun.
  Flag material behavior changes so the reviewer knows what needs another look.
- At handoff, provide one ordered list of PR links from the bottom of each stack up,
  with a one-sentence purpose and ready/draft/blocked status. Identify what to review first.

## Local checks

- Run `make check` from the repository root before handing off a PR. It checks Python
  lint/format and frontend lint/format/types. It does not replace component tests.
- Use `make check-python` or `make check-frontend` while working on one component.
- Frontend fixes: `pnpm --dir frontend lint:fix` and `pnpm --dir frontend format`.
- Python fixes: `uv run --project backend ruff check --config backend/pyproject.toml --fix <paths>`,
  then `uv run --project backend ruff format --config backend/pyproject.toml <paths>`.
- Limit automatic fixes to your owned files; inspect the resulting diff. Regenerate
  generated files rather than formatting them manually. Do not weaken rules to pass a check;
  explain any narrow suppression beside the affected code.

## Runtime boundaries and checks

- `contracts.py` defines wire payloads. Regenerate OpenAPI and TypeScript after contract changes.
- Keep the harness free of I/O. Workers use HTTP, never runtime-table access.
- Commit the tool result and complete workspace checkpoint in one transaction, separate
  from runtime acceptance. Retry from the same saved input version. Tool effects stay
  inside the private workspace; external writes are out of scope for the initial version.
- Do not replace unfinished behavior with successful fake responses. Keep scaffold markers
  accurate and remove them when the implementation is ready.
- Unit tests use recorded responses. Live OpenAI calls belong in explicitly requested checks.
- Run checks for the component you changed. The integration owner runs the durability suite.
