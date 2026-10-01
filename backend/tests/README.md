# Tests

Contract tests cover shared payload validation, API stubs, and generated OpenAPI.
Run `uv run --project backend --locked pytest backend/tests` from the repository root.

Set `TEST_DATABASE_URL` to include PostgreSQL tests. The shared `postgres_engine`
fixture creates and migrates a unique schema for each test, then drops it. It never
uses `metadata.create_all()`. Without the variable, database tests are skipped.

Component owners add tests in their matching directories. Integration owns the
contract tests and cross-service failure cases described in `DESIGN.md`.
Database tests cover migration parity, constraints, and transaction commit/rollback.
Service behavior and failure-injection tests are still pending.
Component owners replace their API scaffold assertions as real handlers land.

Use recorded responses for unit tests. Live OpenAI calls require an explicit request.
