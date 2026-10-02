# Durable Agent

A Next.js UI and Python runtime for agents with recoverable tool execution.
See [DESIGN.md](DESIGN.md) for the architecture and recovery guarantees.

## Run locally

Start Docker Desktop, then run from the repository root:

```bash
cp -n .env.example .env  # Keeps an existing .env.
```

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env`, then:

```bash
./scripts/dev-up.sh
```

This builds the sandbox image, applies database migrations, and starts PostgreSQL,
Control, Tools, two workers, and the frontend. Open **http://localhost:3000**.
Submitting a prompt makes real OpenAI calls using your key.

Try: “Write a Python function that adds two numbers, create a pytest test, and run it.”
Inspect the run's operations, attempts, final answer, and saved files in the UI.

```bash
docker compose ps
docker compose logs -f control tools worker
docker compose down
```

Runs and saved files survive shutdown in the PostgreSQL volume. `down -v` deletes
that data. Rerun `./scripts/dev-up.sh` to start again or rebuild after pulling changes.
API docs are at http://localhost:8000/docs and http://localhost:8001/docs.

Tools uses the local Docker socket to launch isolated sandboxes. Each sandbox gets
only a private workspace; it has no network, credentials, or Docker socket. Temporary
workspaces live in the ignored `.sandbox-workspaces/` directory. Runs start empty.

For another checkout, set a distinct `COMPOSE_PROJECT_NAME` and unused
`FRONTEND_PORT`, `CONTROL_PORT`, `TOOLS_PORT`, and `POSTGRES_PORT` in the shell.

[Crash Test hooks](docs/CRASH_TEST.md) expose precise execution boundaries to a separate
fault controller. They are disabled by default; `compose.crash-test.yaml` opts services in.

To start the app with hooks, then run Crashboard in the background:

```bash
CRASH_TEST=1 ./scripts/dev-up.sh
CRASH_TEST_MODE=docker ./scripts/crash-test.sh
```

Both scripts load the repository's `.env` automatically. To keep these settings,
add `CRASH_TEST=1` and `CRASH_TEST_MODE=docker` there, then run the scripts without
prefixes. Shell variables take precedence, so `CRASH_TEST_MODE=host ./scripts/crash-test.sh`
still selects terminal mode.

Open [Crashboard](http://127.0.0.1:3020/) to choose scenarios and control the
existing app containers. The controller and dashboard run in their own Compose
project, sharing the app's database through its APIs. For live frontend development,
omit `CRASH_TEST_MODE=docker` to run them in the terminal instead.

## Structure

```text
frontend/                       Next.js UI and API proxy
backend/src/durable_agent/
  control/                      Scheduling, leases, and run APIs
  harness/                      Pure agent transitions
  worker/                       OpenAI and tool execution
  tools/                        Files, sandbox runner, and checkpoints
  db/                           Shared models and sessions
  contracts.py                  API payloads
backend/migrations/             Alembic schema revisions
backend/tests/                  Component and database tests
contracts/                      Generated OpenAPI
scripts/                        Startup and API generation
docker/                         Service and sandbox images
```

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

Run tests with `uv run --project backend --locked pytest backend/tests`. Contract
tests need no services or API keys. PostgreSQL tests run when `TEST_DATABASE_URL`
is set; otherwise they are skipped. For the local database above:

```bash
TEST_DATABASE_URL=postgresql://durable:durable@localhost:5432/durable \
  uv run --project backend --locked pytest backend/tests
```

Each database test migrates a unique schema and drops only that schema afterward.
The test user must be able to create schemas. These tests cover schema constraints, transactions, scheduling, and recovery.

Frontend browser tests use Playwright with Chromium. Install its browser once:

```bash
pnpm --dir frontend exec playwright install chromium
pnpm --dir frontend test:e2e
```

Tests live in `frontend/tests/` and start their own Next.js server on
`http://127.0.0.1:3101`. Set `PLAYWRIGHT_PORT` to use another port in a parallel
worktree. An occupied port fails rather than reusing another app. Stop any Next dev
server in the same worktree first because they share `.next/`.

Use `pnpm --dir frontend test:e2e --ui` to run and inspect individual tests, or
`--headed` to watch Chromium. Test fixtures intercept `/api/*`; those tests verify
frontend behavior, not backend integration.

After changing routes or payloads, regenerate types:

```bash
uv run --project backend python scripts/export_openapi.py
pnpm --dir frontend api:generate
```

## Parallel work

[docs/PARALLEL_WORK.md](docs/PARALLEL_WORK.md) assigns component directories and
shared files to their owners. [docs/API.md](docs/API.md) describes the contract handoff.
