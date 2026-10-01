# API contracts

Application contracts have not been implemented. The control and tool services
expose only `/health/live` and `/health/ready`. `contracts.py` is an empty placeholder.

Before component implementation, agree on the payloads for:

- Frontend to control: run submission, status, output, and saved files.
- Worker to control: claims, heartbeats, and reports.
- Worker to tools: execution requests and results.
- Control to harness: state, results, and transitions.

Use [DESIGN.md](../DESIGN.md) for the intended behavior. The integration owner
coordinates shared payloads and route signatures with component owners, then
regenerates OpenAPI and frontend types. Do not treat generated health schemas
as application contracts.

```bash
uv run --project backend python scripts/export_openapi.py
pnpm --dir frontend api:generate
```
