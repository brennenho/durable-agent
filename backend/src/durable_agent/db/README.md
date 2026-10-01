# Database boundary

`models.py` defines the five tables. `session.py` creates an async psycopg engine
and session factory without connecting at import time. Control and Tools each
create one engine at startup and dispose it at shutdown. Workers do not use this module.

The session factory is the database abstraction. Use it directly; no repository or
session wrapper is needed. Create it once per service and inject it into handlers:

```python
engine = create_database_engine(database_url)
db_session = create_session_factory(engine)

# Write: the caller chooses when the transaction commits.
async with db_session() as session:
    session.add(WorkspaceVersion(archive=archive_bytes))
    await session.commit()

# Read: no commit needed.
async with db_session() as session:
    workspace = await session.get(WorkspaceVersion, version_id)

await engine.dispose()  # At service shutdown.
```

Exiting closes the session and rolls back uncommitted work, including on exceptions
or cancellation. A successful `commit()` cannot be undone by a later exception.
For an explicitly scoped transaction, `async with db_session.begin() as session:`
commits on success and rolls back on failure; do not also commit inside that block.

Use the same session type and pool for reads and writes. Separate factories add no
value with one database; add enforced read-only transactions or replica routing only
when needed. A read session here is a convention, not a database permission boundary.

Use one session per request or task; never share a session across concurrent tasks.
Keep HTTP calls and sandbox execution outside transactions. Queries, `flush()`,
`commit()`, and `rollback()` are awaited; `add()` only changes local state.
Autoflush is disabled: flush before queries that need pending writes. Commits flush
automatically. Loaded attributes remain accessible after commit without implicit I/O.

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
