#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

# uv parses dotenv values without executing them; exported shell values win.
if [ -f .env ] && [ "${DURABLE_ENV_LOADED:-0}" != 1 ]; then
    export DURABLE_ENV_LOADED=1
    exec uv run --no-project --env-file .env -- ./scripts/dev-up.sh "$@"
fi
unset DURABLE_ENV_LOADED

if ! command -v docker >/dev/null 2>&1; then
    if [ -x /Applications/Docker.app/Contents/Resources/bin/docker ]; then
        export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
    else
        echo "Docker CLI not found. Install Docker or add its bin directory to PATH." >&2
        exit 1
    fi
fi

set -- -f compose.yaml
if [ "${CRASH_TEST:-0}" = "1" ]; then
    CRASH_TEST_STATE_DIR=${CRASH_TEST_STATE_DIR:-"$(pwd -P)/.crash-test"}
    export CRASH_TEST_STATE_DIR
    uv run --project backend --locked python -c 'from crash_test.host import ensure_token; ensure_token()'
    CRASH_TEST_HOOK_TOKEN=$(cat "$CRASH_TEST_STATE_DIR/token")
    CRASH_TEST_HOOK_URL=${CRASH_TEST_HOOK_URL:-http://host.docker.internal:9020/hooks}
    CRASH_TEST_HOOK_TIMEOUT_SECONDS=30
    export CRASH_TEST_HOOK_TOKEN CRASH_TEST_HOOK_URL CRASH_TEST_HOOK_TIMEOUT_SECONDS
    set -- "$@" -f compose.crash-test.yaml
fi
docker compose "$@" config --quiet

docker build -f docker/Dockerfile.sandbox -t durable-agent-sandbox:dev .
SANDBOX_IMAGE_DIGEST=$(docker image inspect durable-agent-sandbox:dev --format '{{.Id}}')
SANDBOX_WORKSPACE_ROOT="$(pwd -P)/.sandbox-workspaces"
endpoint=${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}
case "$endpoint" in
    unix://*) DOCKER_SOCKET=${endpoint#unix://} ;;
    *) echo "Use a local Docker daemon (Docker Desktop on macOS)." >&2; exit 1 ;;
esac
export SANDBOX_IMAGE_DIGEST SANDBOX_WORKSPACE_ROOT DOCKER_SOCKET
mkdir -p "$SANDBOX_WORKSPACE_ROOT"
docker compose "$@" up --build --detach --scale worker=2 --wait
printf '\nOpen http://localhost:%s\n' "${FRONTEND_PORT:-3000}"
