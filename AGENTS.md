# Working in this repository

Read `DESIGN.md`, `docs/API.md`, and your section in `docs/PARALLEL_WORK.md` first.

- Work in your assigned directories. Keep component tests in the matching test directory.
- The integration owner maintains shared contracts, generated API types, migrations,
  dependency manifests/lockfiles, Compose, and cross-service tests. Coordinate needed
  changes there instead of having several agents edit the same files.
- `contracts.py` defines wire payloads. Regenerate OpenAPI and TypeScript after contract changes.
- Keep the harness free of I/O. Workers use HTTP, never runtime-table access.
- Commit the tool result and complete workspace checkpoint in one transaction, separate
  from runtime acceptance. Retry from the same saved input version. Tool effects stay
  inside the private workspace; external writes are out of scope for the initial version.
- Do not replace unfinished behavior with successful fake responses. Keep scaffold markers
  accurate and remove them when the implementation is ready.
- Unit tests use recorded responses. Live OpenAI calls belong in explicitly requested checks.
- Run checks for the component you changed. The integration owner runs the durability suite.
