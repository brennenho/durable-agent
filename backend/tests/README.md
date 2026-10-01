# Tests

Contract tests cover shared payload validation, API stubs, and generated OpenAPI.
Run them with `uv run --project backend pytest backend/tests` from the repository root.

Component owners add tests in their matching directories. Integration owns the
contract tests and cross-service failure cases described in `DESIGN.md`.
Service behavior and failure-injection tests are still pending.
Component owners replace their API scaffold assertions as real handlers land.

Use recorded responses for unit tests. Live OpenAI calls require an explicit request.
