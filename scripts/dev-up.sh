#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

docker compose config --quiet

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
docker compose up --build --detach --scale worker=2 --wait
printf '\nOpen http://localhost:%s\n' "${FRONTEND_PORT:-3000}"
