#!/bin/sh
set -eu

Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/tmp/xvfb-smoke.log 2>&1 &
xvfb_pid=$!
openbox_pid=
trap 'if [ -n "$openbox_pid" ]; then kill "$openbox_pid" 2>/dev/null || true; fi; kill "$xvfb_pid" 2>/dev/null || true' EXIT

ready=0
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.2
done
if [ "$ready" != "1" ]; then
    cat /tmp/xvfb-smoke.log >&2
    exit 70
fi

DISPLAY=:99 openbox >/tmp/openbox-smoke.log 2>&1 &
openbox_pid=$!
ready=0
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
    if DISPLAY=:99 xprop -root _NET_SUPPORTING_WM_CHECK | grep -q 'window id'; then
        ready=1
        break
    fi
    sleep 0.2
done
if [ "$ready" != "1" ]; then
    cat /tmp/openbox-smoke.log >&2
    exit 70
fi

DISPLAY=:99 python /app/docker/browser_smoke_test.py
