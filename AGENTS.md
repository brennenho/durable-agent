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

## Graphite and stacked PRs

- Use Graphite (`gt`) to create, update, restack, and submit PR branches. Use
  `gt create`, `gt modify`, `gt restack`, and `gt submit`; check installed command
  help for flags. See the [CLI reference](https://graphite.com/docs/command-reference).
  If Graphite is unavailable or unauthenticated, report the blocker; do not silently
  publish through a different workflow.
- Before coding, give a short outline of the proposed PRs and their dependencies.
  Keep one logical, explainable change per PR and one branch per PR.
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
- Keep unfinished work in draft. Mark a PR ready only after self-review and relevant
  checks pass. Leave merging to the human unless explicitly asked to merge; do not
  enable auto-merge or dismiss reviews on their behalf.

## Make review easy

- Review the diff against the immediate parent, including generated files. Remove
  accidental changes, stale comments, and unnecessary churn before submitting.
- Use a concrete title describing the resulting behavior. Describe the final change
  for someone who has not read the chat; omit the agent's work log.
- Keep PR descriptions short and cover:
  - **What and why:** the problem and resulting behavior; a before/after example when useful.
  - **Stack:** the parent dependency and what this PR adds. Identify deferred work explicitly.
  - **Validation:** exact checks run and their results, including anything not verified.
  - **Review notes:** the main entry point, non-obvious decisions, or material risks, when relevant.
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
