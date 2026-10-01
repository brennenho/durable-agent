.PHONY: check check-python check-frontend

check: check-python check-frontend

check-python:
	uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts
	uv run --project backend --locked ruff format --config backend/pyproject.toml --check backend scripts

check-frontend:
	pnpm --dir frontend check
