#!/bin/sh
set -eu

repository=${1:-.}
repository=$(cd "$repository" && pwd)
build_context=$(mktemp -d /tmp/ok-ww-compose.XXXXXX)
compose_started=0

cleanup() {
    if [ "$compose_started" = "1" ] && [ -f "$build_context/compose.yaml" ]; then
        (cd "$build_context" && docker compose down --volumes --remove-orphans) \
            >/dev/null 2>&1 || true
    fi
    case "$build_context" in
        /tmp/ok-ww-compose.*) rm -rf "$build_context" ;;
        *) echo "refusing to clean unexpected path: $build_context" >&2 ;;
    esac
}
trap cleanup EXIT

cd "$repository"
tar --exclude-from=.dockerignore -cf - . | tar -xf - -C "$build_context"
cd "$build_context"
docker compose config --quiet
docker compose build cloud-runner
compose_started=1
docker compose run --rm --no-deps --entrypoint /bin/sh cloud-runner \
    /app/docker/browser_smoke_test.sh
CLOUD_COMMAND=serve docker compose up -d cloud-runner

ready=0
for _attempt in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:17880/healthz >/tmp/ok-ww-health.json; then
        ready=1
        break
    fi
    sleep 2
done
if [ "$ready" != "1" ]; then
    docker compose logs --tail=160 cloud-runner >&2
    exit 71
fi

grep -q '"status":"ok"' /tmp/ok-ww-health.json
curl -fsS http://127.0.0.1:17880/api/tasks | grep -q 'DailyTask'
curl -fsS http://127.0.0.1:17880/api/schedule | grep -q 'src.task.DailyTask.DailyTask'
curl -fsS http://127.0.0.1:17880/api/task-tabs | grep -q 'character-code'
curl -fsS http://127.0.0.1:17880/task-tabs/character-code/assets/index.js >/dev/null
curl -fsS http://127.0.0.1:17880/api/about \
    | grep -q '"update_supported":false'
curl -fsS http://127.0.0.1:17880/api/updates \
    | grep -q '"update_available":false'
curl -fsS http://127.0.0.1:17880/api/docker-update \
    | grep -q '"current_version":"v1.0.7"'
if curl -fsS -X POST http://127.0.0.1:17880/api/docker-update >/dev/null 2>&1; then
    echo 'Docker update endpoint accepted a request without confirmation' >&2
    exit 74
fi
curl -fsS http://127.0.0.1:15980/vnc.html >/dev/null
docker compose exec -T cloud-runner python - <<'PY'
import asyncio

import websockets


async def main():
    async with websockets.connect(
        "ws://127.0.0.1:15980/websockify",
        subprotocols=["binary"],
        open_timeout=10,
    ) as websocket:
        banner = await asyncio.wait_for(websocket.recv(), timeout=5)
        assert banner == b"RFB 003.008\n", banner


asyncio.run(main())
PY
docker compose exec -T cloud-runner python /app/docker/management_ui_smoke_test.py
if docker compose logs --no-color cloud-runner \
    | grep -Eq 'SetProcessDpiAwareness error|calling pyappify.get_version_list'; then
    docker compose logs --tail=160 cloud-runner >&2
    exit 73
fi
docker compose restart cloud-runner

ready=0
for _attempt in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:17880/healthz >/tmp/ok-ww-health-restarted.json; then
        ready=1
        break
    fi
    sleep 2
done
if [ "$ready" != "1" ]; then
    docker compose logs --tail=160 cloud-runner >&2
    exit 72
fi
grep -q '"status":"ok"' /tmp/ok-ww-health-restarted.json
printf '%s\n' 'docker-web-smoke health=ok tasks=ok schedule=ok character-code=ok updates=ok novnc=ok'
docker compose down --volumes --remove-orphans
compose_started=0
