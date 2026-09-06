"""Select enroll or serve from the persistent enrollment marker."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def select_command(data_dir: str, cloud_url: str) -> str:
    marker = Path(data_dir) / "enrollment.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "enroll"
    if payload.get("enrolled") is True and payload.get("cloud_url") == cloud_url:
        return "serve"
    return "enroll"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: select_cloud_command.py DATA_DIR CLOUD_URL")
    print(select_command(sys.argv[1], sys.argv[2]))
