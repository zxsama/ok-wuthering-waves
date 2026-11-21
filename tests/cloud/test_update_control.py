import time
from pathlib import Path

import pytest

from extensions.cloud.update_control import DockerUpdateBusy, DockerUpdateControl


def test_update_control_uses_fallback_version_before_updater_starts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OK_WW_BUILD_REVISION", "unknown")
    control = DockerUpdateControl(tmp_path, fallback_version="v1.0.6")

    assert control.status() == {
        "enabled": False,
        "status": "idle",
        "message": "",
        "current_version": "v1.0.6",
        "current_revision": None,
    }


def test_manual_update_request_is_persisted_for_updater(tmp_path: Path, monkeypatch):
    revision = "70857e1184e1df88d845724bbe343586efd3af8a"
    monkeypatch.setenv("OK_WW_BUILD_REVISION", revision)
    directory = tmp_path / "update-control"
    directory.mkdir()
    (directory / "available").write_text(f"{time.time()}\n", encoding="utf-8")
    (directory / "status").write_text("up_to_date\n", encoding="utf-8")

    control = DockerUpdateControl(tmp_path, fallback_version="dev")
    result = control.request_update()

    assert (directory / "request").read_text(encoding="utf-8").strip()
    assert (directory / "status").read_text(encoding="utf-8").strip() == "up_to_date"
    assert control.status() == result
    assert result == {
        "enabled": True,
        "status": "queued",
        "message": "Manual Docker update requested",
        "current_version": "dev",
        "current_revision": revision,
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


@pytest.mark.parametrize("state", ["checking", "deferred", "updating", "rebuilding"])
def test_manual_update_preserves_updater_progress(tmp_path, state):
    control = DockerUpdateControl(tmp_path)
    control.directory.mkdir()
    (control.directory / "available").write_text(str(time.time()))
    (control.directory / "status").write_text(state)
    with pytest.raises(DockerUpdateBusy):
        control.request_update()
    assert control.status()["status"] == state
    assert not (control.directory / "request").exists()


def test_recreated_web_observes_pending_or_claimed_update(tmp_path):
    control = DockerUpdateControl(tmp_path)
    control.directory.mkdir()
    (control.directory / "available").write_text(str(time.time()))
    control.request_update()
    reopened = DockerUpdateControl(tmp_path)
    for name in ("request", "request.in-progress"):
        if name == "request.in-progress":
            (control.directory / "request").rename(control.directory / name)
        assert reopened.update_in_progress()
        with pytest.raises(DockerUpdateBusy):
            reopened.request_update()
    (control.directory / "status").write_text("rebuilding")
    assert reopened.status()["status"] == "rebuilding"


@pytest.mark.parametrize("already_busy", [False, True])
def test_runtime_publishes_busy_before_checking_update(tmp_path, monkeypatch, already_busy):
    control = DockerUpdateControl(tmp_path)
    marker = control.directory / "runtime-busy"
    if already_busy:
        control.set_runtime_busy(True)

    def check():
        assert marker.is_file()
        return True

    monkeypatch.setattr(control, "update_in_progress", check)
    with pytest.raises(DockerUpdateBusy):
        control.reserve_runtime()
    assert marker.is_file() == already_busy
