# Database boundary

`models.py` defines the five tables. `session.py` creates an async psycopg engine
and session factory without connecting at import time. Control and Tools each
create one engine at startup and dispose it at shutdown. Workers do not use this module.

Use one session per request or background task. Choose the transaction explicitly:

```python
engine = create_database_engine(database_url)
sessions = create_session_factory(engine)

async with sessions.begin() as session:
    # Lock, read, and write the owning service's rows here.
    ...

await engine.dispose()
```

The context commits on success and rolls back on exceptions. A plain `sessions()`
context does not commit. Autoflush is disabled; call `flush()` before queries that
need pending writes. Keep network calls and sandbox execution outside transactions.

- Control writes `runs`, `operations`, and `attempts`.
- Tools writes `tool_requests` and `workspace_versions`.
- PostgreSQL enforces foreign keys, uniqueness, and consistent completion/status fields.
- Services enforce input/result immutability, lease fencing, and atomic progress updates.
  These behaviors remain to be implemented; there are no triggers or automatic retries.

Serialize contracts with `model_dump(mode="json")` for JSONB. Assign complete JSON
values; in-place dictionary edits are not tracked. `None` is stored as SQL `NULL`,
including incomplete results. Preserve saved workspace versions and tool results.

Migrations live in `backend/migrations/`; Integration owns the single revision chain.
Run migrations explicitly before starting services. Do not call `metadata.create_all()`
at service startup. See the root README for migration and test commands.
