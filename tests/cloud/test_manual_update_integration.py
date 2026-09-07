from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from extensions.cloud.config import CloudSettings
from extensions.cloud.web_service import create_cloud_web_app


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _Adapter:
    def call_page(self, callback):
        raise AssertionError("the update endpoint must not access the cloud-game page")


class _ScheduleManager:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for the updater integration condition")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _wsl_path(path: Path) -> str:
    windows_path = str(path.resolve()).replace("\\", "/")
    return subprocess.run(
        ["wsl", "wslpath", "-a", windows_path],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _start_updater(
    *, repository: Path, control: Path, fake_bin: Path, command_log: Path, stop: Path
) -> subprocess.Popen[str]:
    script = REPOSITORY_ROOT / "docker" / "auto_update.sh"
    variables = {
        "CLOUD_UPDATE_REPOSITORY_DIR": repository,
        "CLOUD_UPDATE_COMPOSE_FILE": repository / "compose.yaml",
        "CLOUD_UPDATE_CONTROL_DIR": control,
        "CLOUD_UPDATE_INTERVAL_SECONDS": "86400",
        "CLOUD_UPDATE_POLL_SECONDS": "1",
        "CLOUD_UPDATE_PROJECT_NAME": "update-integration-test",
        "CLOUD_UPDATE_TEST_LOG": command_log,
        "CLOUD_UPDATE_TEST_STOP": stop,
    }

    if os.name == "nt":
        executable = ["wsl", "-u", "root", "env"]
        converted = {
            name: _wsl_path(value) if isinstance(value, Path) else value
            for name, value in variables.items()
        }
        path = f"{_wsl_path(fake_bin)}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        command = executable + [f"PATH={path}"] + [
            f"{name}={value}" for name, value in converted.items()
        ] + ["sh", _wsl_path(script)]
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    environment = os.environ.copy()
    environment.update({name: str(value) for name, value in variables.items()})
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    return subprocess.Popen(
        ["sh", str(script)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.skipif(
    os.name == "nt" and not (Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/wsl.exe").exists(),
    reason="WSL is required to run the POSIX updater script on Windows",
)
def test_web_manual_update_request_is_consumed_by_auto_updater(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    control = tmp_path / "control"
    fake_bin = tmp_path / "bin"
    command_log = tmp_path / "docker-commands.log"
    stop = tmp_path / "stop"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (repository / "VERSION").write_text("v-test\n", encoding="utf-8")
    (repository / "docker").mkdir()
    (repository / "docker" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    control.mkdir()
    (control / "runtime-busy").write_text("stale\n", encoding="utf-8")
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "git",
        """#!/bin/sh
case "$*" in
  *"status --porcelain"*) exit 0 ;;
  *"fetch --quiet"*) exit 0 ;;
  *"rev-parse HEAD"*) printf 'same-revision\\n' ;;
  *"rev-parse FETCH_HEAD"*) printf 'same-revision\\n' ;;
  *"describe --tags --always --dirty"*) printf 'v-test\\n' ;;
  *) exit 0 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/bin/sh
printf '%s\\n' "$*" >>"$CLOUD_UPDATE_TEST_LOG"
case "$*" in
  *"compose"*"config --images cloud-runner"*) printf 'test/cloud-runner:dev\\n' ;;
esac
""",
    )
    _write_executable(
        fake_bin / "sleep",
        """#!/bin/sh
if [ -f "$CLOUD_UPDATE_TEST_STOP" ]; then
  exit 99
fi
/bin/sleep 0.05
""",
    )

    updater = _start_updater(
        repository=repository,
        control=control,
        fake_bin=fake_bin,
        command_log=command_log,
        stop=stop,
    )
    app = None
    server = None
    server_thread = None
    try:
        _wait_until(
            lambda: (control / "status").exists()
            and (control / "status").read_text(encoding="utf-8").strip()
            == "up_to_date"
        )
        assert not (control / "runtime-busy").exists()
        initial_commands = command_log.read_text(encoding="utf-8")
        assert "build --platform linux/amd64" not in initial_commands

        monkeypatch.setenv("OK_WW_UPDATE_CONTROL_DIR", str(control))
        app = create_cloud_web_app(
            CloudSettings(data_dir=tmp_path / "cloud-data"),
            _Adapter(),
            schedule_manager_factory=_ScheduleManager,
        )
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",
                lifespan="off",
            )
        )
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        _wait_until(lambda: server.started)

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/docker-update",
            method="POST",
            headers={"X-OK-WW-Update": "1"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
            assert response.status == 202
        assert payload["status"] == "queued"

        _wait_until(
            lambda: command_log.exists()
            and "build --platform linux/amd64" in command_log.read_text(encoding="utf-8")
            and "up -d --no-deps --force-recreate --wait"
            in command_log.read_text(encoding="utf-8")
        )
        commands = command_log.read_text(encoding="utf-8")
        assert "--project-name update-integration-test" in commands
        _wait_until(
            lambda: (control / "status").read_text(encoding="utf-8").strip()
            == "up_to_date"
        )
        assert (control / "status").read_text(encoding="utf-8").strip() == "up_to_date"
        assert not (control / "request").exists()
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=5)
        if app is not None:
            app.state.cloud_controller.close()
        stop.touch()
        try:
            updater.wait(timeout=5)
        except subprocess.TimeoutExpired:
            updater.terminate()
            updater.wait(timeout=5)
        if updater.returncode not in {0, 99}:
            stdout, stderr = updater.communicate()
            pytest.fail(f"updater failed with {updater.returncode}:\n{stdout}\n{stderr}")
