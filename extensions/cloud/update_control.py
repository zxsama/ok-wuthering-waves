"""File-based bridge between the Web container and the Docker updater."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


UPDATE_CONTROL_ENV = "OK_WW_UPDATE_CONTROL_DIR"
UPDATE_HEARTBEAT_TIMEOUT_ENV = "OK_WW_UPDATE_HEARTBEAT_TIMEOUT"
BUSY_UPDATE_STATES = {"queued", "checking", "deferred", "updating", "rebuilding"}


class DockerUpdateControl:
    def __init__(self, data_dir: Path, fallback_version: str = "dev") -> None:
        configured = os.environ.get(UPDATE_CONTROL_ENV, "").strip()
        self.directory = Path(configured) if configured else data_dir / "update-control"
        self.fallback_version = fallback_version

    def status(self) -> dict[str, object]:
        heartbeat = self._read_float("available")
        try:
            heartbeat_timeout = float(os.environ.get(UPDATE_HEARTBEAT_TIMEOUT_ENV, "120"))
        except ValueError:
            heartbeat_timeout = 120.0
        return {
            "enabled": heartbeat is not None and time.time() - heartbeat <= heartbeat_timeout,
            "status": self._read("status", "idle"),
            "message": self._read("message", ""),
            "current_version": self.fallback_version,
        }

    def request_update(self) -> dict[str, object]:
        if not self.status()["enabled"]:
            raise RuntimeError("Docker auto-updater is not available")
        self._write("status", "queued")
        self._write("message", "Manual Docker update requested")
        self._write("request", f"{time.time():.6f}")
        result = self.status()
        return result

    def update_in_progress(self) -> bool:
        status = self.status()
        return bool(status["enabled"] and status["status"] in BUSY_UPDATE_STATES)

    def set_runtime_busy(self, busy: bool) -> None:
        marker = self.directory / "runtime-busy"
        if busy:
            self._write("runtime-busy", f"{time.time():.6f}")
        else:
            marker.unlink(missing_ok=True)

    def _read(self, name: str, default: str) -> str:
        try:
            value = (self.directory / name).read_text(encoding="utf-8").strip()
        except OSError:
            return default
        return value or default

    def _read_float(self, name: str) -> float | None:
        try:
            return float(self._read(name, ""))
        except ValueError:
            return None

    def _write(self, name: str, value: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / name
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{name}.",
                delete=False,
            ) as temporary:
                temporary.write(f"{value}\n")
                temporary_path = Path(temporary.name)
            temporary_path.replace(target)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
