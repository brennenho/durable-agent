# Durable Agent

Monorepo scaffold for a Next.js frontend and Python backend.
The planned architecture is in [DESIGN.md](DESIGN.md).

## Structure

```text
frontend/                       Next.js app and placeholder page
backend/
  src/durable_agent/
    control/                    Typed FastAPI routes; handlers pending
    harness/                    State/transition types; agent loop pending
    worker/                     Placeholder entry point
    tools/                      Typed FastAPI routes; handlers pending
    db/                         Empty package for database code
    contracts.py                Shared API payloads and tool argument types
  migrations/                   Empty; no schema or migration setup
  tests/                        Contract tests; component/integration behavior pending
contracts/                      Generated OpenAPI
scripts/                        API export
docker/                         Dockerfiles
compose.yaml                    Local services
```

`frontend/` owns its pnpm dependencies; `backend/` owns its uv dependencies.
The manifests include the planned stack. Shared payloads and route signatures are
implemented. Database models, queries, migrations, agent logic, and tools are pending.

The APIs expose liveness (`200`), readiness (`503`), and typed application stubs (`501`).
Compose checks liveness so the scaffold can start; application readiness comes later.
The worker is a placeholder and is excluded from default startup.

## Run locally

With Docker running:

```bash
cp .env.example .env
docker compose up --build frontend control tools
```

Open `http://localhost:3000`. API docs are at `http://localhost:8000/docs`
and `http://localhost:8001/docs`. No model calls or database operations run.

An optional PostgreSQL container is configured for later development:

```bash
docker compose up -d db
```

Its named volume survives `docker compose down`; `down -v` deletes it.

## Develop without containers

Use Node 24, pnpm 11.25.0, Python 3.12+, and uv. From the repository root:

```bash
pnpm --dir frontend install --frozen-lockfile
uv sync --project backend --locked
pnpm --dir frontend dev
```

Run the API entry points in separate terminals:

```bash
uv run --project backend uvicorn durable_agent.control.app:app --port 8000
uv run --project backend uvicorn durable_agent.tools.app:app --port 8001
```

Local commands read environment variables from your shell; Compose reads `.env`.

## Checks and API generation

Run all local code checks from the repository root:

```bash
make check
```

This runs Ruff linting/format checks across Python source, tests, migrations, and
scripts, followed by ESLint, Prettier, and TypeScript checks for the frontend.
Use `make check-python` or `make check-frontend` to check one side.
No services, database, or API keys are needed. Checks do not rewrite source files.

| Task | Command |
| --- | --- |
| Fix frontend lint | `pnpm --dir frontend lint:fix` |
| Format frontend | `pnpm --dir frontend format` |
| Check frontend types only | `pnpm --dir frontend typecheck` |
| Fix Python lint | `uv run --project backend ruff check --config backend/pyproject.toml --fix <paths>` |
| Format Python | `uv run --project backend ruff format --config backend/pyproject.toml <paths>` |

Pass only your owned files when fixing Python. For a frontend file, use
`pnpm --dir frontend exec prettier --write src/path/to/file.tsx` or
`pnpm --dir frontend exec eslint --fix src/path/to/file.tsx`.
Generated API types, Next.js generated files, and lockfiles are excluded from frontend
linting/formatting. `frontend/pnpm-workspace.yaml` allows the lint import resolver's
native install step; the frontend remains a standalone project.

Validate Compose changes with `docker compose config --quiet`. These checks are local;
CI and Git hooks are not configured.

Run contract tests with `uv run --project backend pytest backend/tests`; no services or API keys are needed.
After changing routes or payloads, regenerate types:

```bash
uv run --project backend python scripts/export_openapi.py
pnpm --dir frontend api:generate
```

## Parallel work

[docs/PARALLEL_WORK.md](docs/PARALLEL_WORK.md) assigns component directories and
shared files to their owners. [docs/API.md](docs/API.md) describes the contract handoff.
