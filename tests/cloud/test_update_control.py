import time
from pathlib import Path

import pytest

from extensions.cloud.update_control import DockerUpdateControl


def test_update_control_uses_fallback_version_before_updater_starts(tmp_path: Path):
    control = DockerUpdateControl(tmp_path, fallback_version="v1.0.6")

    assert control.status() == {
        "enabled": False,
        "status": "idle",
        "message": "",
        "current_version": "v1.0.6",
    }


def test_manual_update_request_is_persisted_for_updater(tmp_path: Path):
    directory = tmp_path / "update-control"
    directory.mkdir()
    (directory / "available").write_text(f"{time.time()}\n", encoding="utf-8")
    (directory / "status").write_text("up_to_date\n", encoding="utf-8")

    control = DockerUpdateControl(tmp_path, fallback_version="dev")
    result = control.request_update()

    assert (directory / "request").read_text(encoding="utf-8").strip()
    assert (directory / "status").read_text(encoding="utf-8").strip() == "queued"
    assert result == {
        "enabled": True,
        "status": "queued",
        "message": "Manual Docker update requested",
        "current_version": "dev",
    }


def test_manual_update_requires_running_updater(tmp_path: Path):
    control = DockerUpdateControl(tmp_path, fallback_version="v1.0.6")

    with pytest.raises(RuntimeError, match="not available"):
        control.request_update()


def test_stale_updater_heartbeat_is_not_available(tmp_path: Path):
    directory = tmp_path / "update-control"
    directory.mkdir()
    (directory / "available").write_text("1\n", encoding="utf-8")

    control = DockerUpdateControl(tmp_path, fallback_version="v1")

    assert control.status()["enabled"] is False


def test_stale_busy_status_does_not_block_runtime(tmp_path: Path):
    directory = tmp_path / "update-control"
    directory.mkdir()
    (directory / "available").write_text("1\n", encoding="utf-8")
    (directory / "status").write_text("rebuilding\n", encoding="utf-8")

    control = DockerUpdateControl(tmp_path, fallback_version="v1")

    assert control.update_in_progress() is False
