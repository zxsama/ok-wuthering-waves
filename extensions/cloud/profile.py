"""Persistent browser-profile location and enrollment marker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProfileStore:
    root: Path

    @property
    def browser_profile_dir(self) -> Path:
        return self.root / "browser-profile"

    @property
    def enrollment_marker(self) -> Path:
        return self.root / "enrollment.json"

    def prepare(self) -> Path:
        self.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        return self.browser_profile_dir

    def is_enrolled(self, cloud_url: str) -> bool:
        try:
            payload = json.loads(self.enrollment_marker.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        return (
            payload.get("schema") == 1
            and payload.get("enrolled") is True
            and payload.get("cloud_url") == cloud_url
        )

    def mark_enrolled(self, cloud_url: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "enrolled": True,
            "cloud_url": cloud_url,
            "enrolled_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = self.enrollment_marker.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.enrollment_marker)
