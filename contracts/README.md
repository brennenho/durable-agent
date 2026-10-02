# Generated API schemas

These files describe the implemented public control API, internal worker API,
tool API, and Crash Test API.

Application payloads live in `backend/src/durable_agent/contracts.py`; Crash Test
payloads live in `backend/src/crash_test/contracts.py`.
FastAPI routes define paths and responses.
The integration owner regenerates schemas and frontend types after contract changes:

```bash
uv run --project backend python scripts/export_openapi.py
pnpm --dir frontend api:generate
```

Do not edit generated files by hand. See `docs/PARALLEL_WORK.md` for ownership.
