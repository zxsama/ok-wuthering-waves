"""File-based bridge between the Web container and the Docker updater."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


UPDATE_CONTROL_ENV = "OK_WW_UPDATE_CONTROL_DIR"
UPDATE_HEARTBEAT_TIMEOUT_ENV = "OK_WW_UPDATE_HEARTBEAT_TIMEOUT"
BUSY_UPDATE_STATES = {"queued", "checking", "deferred", "updating", "rebuilding"}


class DockerUpdateBusy(RuntimeError):
    pass


class DockerUpdateControl:
    def __init__(self, data_dir: Path, fallback_version: str = "dev") -> None:
        configured = os.environ.get(UPDATE_CONTROL_ENV, "").strip()
        self.directory = Path(configured) if configured else data_dir / "update-control"
        self.fallback_version = fallback_version
        revision = os.environ.get("OK_WW_BUILD_REVISION", "").strip()
        self.revision = revision if revision not in {"", "dev", "unknown"} else None

    def status(self) -> dict[str, object]:
        heartbeat = self._read_float("available")
        try:
            heartbeat_timeout = float(os.environ.get(UPDATE_HEARTBEAT_TIMEOUT_ENV, "120"))
        except ValueError:
            heartbeat_timeout = 120.0
        pending = (self.directory / "request").exists() or (self.directory / "request.in-progress").exists()
        state = self._read("status", "idle")
        message = self._read("message", "")
        if pending and state not in BUSY_UPDATE_STATES:
            state, message = "queued", "Manual Docker update requested"
        return {
            "enabled": heartbeat is not None and time.time() - heartbeat <= heartbeat_timeout,
            "status": state,
            "message": message,
            "current_version": self.fallback_version,
            "current_revision": self.revision,
        }

    def request_update(self) -> dict[str, object]:
        status = self.status()
        if not status["enabled"]:
            raise RuntimeError("Docker auto-updater is not available")
        if status["status"] in BUSY_UPDATE_STATES:
            raise DockerUpdateBusy("A Docker update is in progress")
        # The updater owns status and message. Publish one request without
        # overwriting progress from a periodic check that just started.
        try:
            with (self.directory / "request").open("x", encoding="utf-8") as request:
                request.write(f"{time.time():.6f}\n")
        except FileExistsError as exc:
            raise DockerUpdateBusy("A Docker update is in progress") from exc
        return {**status, "status": "queued", "message": "Manual Docker update requested"}

    def update_in_progress(self) -> bool:
        status = self.status()
        return bool(status["enabled"] and status["status"] in BUSY_UPDATE_STATES)

    def set_runtime_busy(self, busy: bool) -> None:
        marker = self.directory / "runtime-busy"
        if busy:
            self._write("runtime-busy", f"{time.time():.6f}")
        else:
            marker.unlink(missing_ok=True)

    def reserve_runtime(self) -> None:
        # The updater publishes "checking" before inspecting this marker.
        # Publish our intent first as well so the two starts cannot pass each other.
        marker = self.directory / "runtime-busy"
        already_busy = marker.exists()
        if not already_busy:
            self.set_runtime_busy(True)
        if self.update_in_progress():
            if not already_busy:
                self.set_runtime_busy(False)
            raise DockerUpdateBusy("A Docker update is in progress")

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
