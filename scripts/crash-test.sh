#!/bin/sh
# The application must already exist. Docker mode starts only its crash-test controls.
set -eu
if [ "$#" -ne 0 ]; then
    echo 'Usage: ./scripts/crash-test.sh (configure with CRASH_TEST_* environment variables)' >&2
    exit 2
fi
cd "$(dirname "$0")/.."
state=${CRASH_TEST_STATE_DIR:-"$(pwd -P)/.crash-test"}
mkdir -p "$state"
state=$(cd "$state" && pwd -P)
port=${CRASH_TEST_PORT:-9020}
export CRASH_TEST_STATE_DIR="$state"
export CRASH_TEST_PORT="$port"
export CRASH_TEST_URL="http://127.0.0.1:$port"
export CRASH_TEST_TOKEN_FILE="$state/token"
case "${CRASH_TEST_MODE:-host}" in
    docker) exec uv run --project backend --locked python -m crash_test.compose ;;
    host) ;;
    *) echo 'CRASH_TEST_MODE must be host or docker' >&2; exit 2 ;;
esac
uv run --project backend --locked python -m crash_test.host &
controller=$!
dashboard=
cleanup() {
    if [ -n "$dashboard" ]; then
        kill -TERM "$dashboard" 2>/dev/null || true
        wait "$dashboard" 2>/dev/null || true
    fi
    kill -TERM "$controller" 2>/dev/null || true
    wait "$controller" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
attempt=0
until uv run --project backend --locked python -c '
import os
from pathlib import Path
import httpx

token = Path(os.environ["CRASH_TEST_TOKEN_FILE"]).read_text().strip()
response = httpx.get(
    os.environ["CRASH_TEST_URL"] + "/snapshot",
    headers={"Authorization": "Bearer " + token},
    timeout=2,
)
response.raise_for_status()
' >/dev/null 2>&1; do
    kill -0 "$controller" 2>/dev/null || { wait "$controller"; exit 1; }
    attempt=$((attempt + 1))
    [ "$attempt" -lt 15 ] || { echo "Controller did not become ready" >&2; exit 1; }
    sleep 1
done
kill -0 "$controller" 2>/dev/null || { wait "$controller"; exit 1; }
printf 'Dashboard: http://127.0.0.1:%s/crash-test\n' "${CRASH_TEST_DASHBOARD_PORT:-3020}"
pnpm --dir frontend exec next dev --hostname 127.0.0.1 --port "${CRASH_TEST_DASHBOARD_PORT:-3020}" &
dashboard=$!
wait "$dashboard"
