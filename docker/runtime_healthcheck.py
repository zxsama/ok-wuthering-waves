"""Health probe for the Docker display, VNC transport, and management API."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.request


def _check_tcp(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=3):
        pass


def main() -> None:
    command = sys.argv[1]
    web_port = int(sys.argv[2])
    novnc_enabled = sys.argv[3] == "1"
    novnc_port = int(sys.argv[4])

    subprocess.run(
        ["xdpyinfo", "-display", os.environ.get("DISPLAY", ":99")],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    if novnc_enabled:
        _check_tcp(5900)
        _check_tcp(novnc_port)
    if command == "serve":
        with urllib.request.urlopen(
            f"http://127.0.0.1:{web_port}/healthz", timeout=5
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"management health returned {response.status}")


if __name__ == "__main__":
    main()
