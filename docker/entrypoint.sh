#!/bin/sh
set -eu

display_number="${DISPLAY:-:99}"
data_dir="${OK_WW_CLOUD_DATA_DIR:-/data/cloud}"
adapter="${OK_WW_CLOUD_ADAPTER:-docker.playwright_adapter:create_playwright_adapter}"
task_runner="${OK_WW_CLOUD_TASK_RUNNER:-extensions.cloud.task_runner:create_daily_task_runner}"
command_name="${CLOUD_COMMAND:-serve}"

mkdir -p "$data_dir"
export DISPLAY="$display_number"

display_id=${DISPLAY#:}
display_id=${display_id%%.*}
case "$display_id" in
    ''|*[!0-9]*)
        echo "DISPLAY must use a numeric local display such as :99" >&2
        exit 64
        ;;
esac
# Docker restart preserves /tmp inside the container. Remove only the exact
# stale X server files for the configured numeric display before starting it.
rm -f "/tmp/.X${display_id}-lock" "/tmp/.X11-unix/X${display_id}"

Xvfb "$DISPLAY" -screen 0 "${CLOUD_SCREEN:-1280x720x24}" -nolisten tcp &
ready=0
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.2
done
if [ "$ready" != "1" ]; then
    echo "Xvfb did not become ready on $DISPLAY" >&2
    exit 70
fi
openbox >/tmp/openbox.log 2>&1 &

if [ "${CLOUD_ENABLE_NOVNC:-1}" = "1" ]; then
    x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 \
        >/tmp/x11vnc.log 2>&1 &
    websockify --web=/usr/share/novnc/ "${CLOUD_NOVNC_INTERNAL_PORT:-15980}" localhost:5900 \
        >/tmp/novnc.log 2>&1 &
fi

set -- python -m extensions.cloud \
    --adapter "$adapter" \
    --data-dir "$data_dir"

if [ "${OK_WW_CLOUD_HEADLESS:-0}" = "1" ]; then
    set -- "$@" --headless
fi

case "$command_name" in
    enroll)
        set -- "$@" enroll
        ;;
    run-once)
        set -- "$@" run-once --task-runner "$task_runner"
        ;;
    schedule)
        set -- "$@" schedule \
            --at "${RUN_AT:-04:00}" \
            --timezone "${TZ:-Asia/Shanghai}" \
            --missed-window-minutes "${MISSED_WINDOW_MINUTES:-120}" \
            --task-runner "$task_runner"
        ;;
    serve)
        set -- "$@" serve \
            --host "${CLOUD_WEB_HOST:-0.0.0.0}" \
            --port "${CLOUD_WEB_PORT:-8765}"
        ;;
    *)
        echo "CLOUD_COMMAND must be enroll, run-once, schedule, or serve" >&2
        exit 64
        ;;
esac

exec "$@"
