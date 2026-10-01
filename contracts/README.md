# Generated API schemas

These files describe the public control API, internal worker API, and tool API.
Application routes are typed stubs that return `501` until their owners implement them.

Shared payloads live in `backend/src/durable_agent/contracts.py`.
FastAPI routes define paths and responses.
The integration owner regenerates schemas and frontend types after contract changes:

```bash
uv run --project backend python scripts/export_openapi.py
pnpm --dir frontend api:generate
```

Do not edit generated files by hand. See `docs/PARALLEL_WORK.md` for ownership.
