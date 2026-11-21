from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

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
    *, repository: Path, control: Path, fake_bin: Path, command_log: Path, stop: Path,
    retry_seconds: str = "300",
    interval_seconds: str = "86400",
) -> subprocess.Popen[str]:
    script = REPOSITORY_ROOT / "docker" / "auto_update.sh"
    variables = {
        "CLOUD_UPDATE_REPOSITORY_DIR": repository,
        "CLOUD_UPDATE_COMPOSE_FILE": repository / "compose.yaml",
        "CLOUD_UPDATE_CONTROL_DIR": control,
        "CLOUD_UPDATE_INTERVAL_SECONDS": interval_seconds,
        "CLOUD_UPDATE_POLL_SECONDS": "1",
        "CLOUD_UPDATE_PROJECT_NAME": "update-integration-test",
        "CLOUD_UPDATE_RETRY_SECONDS": retry_seconds,
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


@pytest.fixture
def updater_sandbox(tmp_path: Path):
    if os.name == "nt" and not (
        Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/wsl.exe"
    ).exists():
        pytest.skip("WSL is required to run the POSIX updater script on Windows")
    repository = tmp_path / "repository"
    control = tmp_path / "control"
    fake_bin = tmp_path / "bin"
    command_log = tmp_path / "commands.log"
    stop = tmp_path / "stop"
    for path in (repository, repository / ".git", control, fake_bin):
        path.mkdir()
    (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (repository / "VERSION").write_text("v-test\n", encoding="utf-8")
    (control / "head").write_text("old-revision\n", encoding="utf-8")
    (control / "remote").write_text("new-revision\n", encoding="utf-8")
    _write_executable(fake_bin / "git", """#!/bin/sh
printf 'git %s\\n' "$*" >>"$CLOUD_UPDATE_TEST_LOG"
state="$CLOUD_UPDATE_CONTROL_DIR"
case "$*" in
  *"status --porcelain"*)
    if [ -f "$state/dirty" ]; then printf ' M tracked-file\\n'; fi ;;
  *"fetch --quiet"*) [ ! -f "$state/offline" ] ;;
  *"rev-parse HEAD"*) cat "$state/head" ;;
  *"rev-parse FETCH_HEAD"*) cat "$state/remote" ;;
  *"merge-base --is-ancestor"*) [ ! -f "$state/non-fast-forward" ] ;;
  *"merge --ff-only"*) cp "$state/remote" "$state/head" ;;
esac
""")
    _write_executable(fake_bin / "docker", """#!/bin/sh
printf 'docker %s\\n' "$*" >>"$CLOUD_UPDATE_TEST_LOG"
state="$CLOUD_UPDATE_CONTROL_DIR"
case "$*" in
  *"config --images cloud-runner"*) printf 'test/cloud-runner:dev\\n' ;;
  "build "*)
    printf 'builder=%s\\n' "${DOCKER_BUILDKIT:-}" >>"$CLOUD_UPDATE_TEST_LOG"
    if [ -f "$state/fail-revision" ] && [ "$(cat "$state/fail-revision")" = "$(cat "$state/head")" ]; then
      exit 1
    fi
    [ "$DOCKER_BUILDKIT" = "1" ] && [ ! -f "$state/fail-build" ] ;;
  *"up -d "*) [ ! -f "$state/fail-up" ] ;;
  *"ps -q cloud-runner"*) printf 'running-container\\n' ;;
  *".State.Running"*) printf 'true\\n' ;;
esac
""")
    _write_executable(fake_bin / "sleep", """#!/bin/sh
if [ -f "$CLOUD_UPDATE_TEST_STOP" ]; then exit 99; fi
/bin/sleep 0.05
""")
    _write_executable(fake_bin / "date", """#!/bin/sh
if [ -f "$CLOUD_UPDATE_CONTROL_DIR/clock" ]; then
  cat "$CLOUD_UPDATE_CONTROL_DIR/clock"
else
  /bin/date "$@"
fi
""")
    processes = []

    def start(*, interval_seconds="86400"):
        stop.unlink(missing_ok=True)
        process = _start_updater(
            repository=repository, control=control, fake_bin=fake_bin,
            command_log=command_log, stop=stop, retry_seconds="1",
            interval_seconds=interval_seconds,
        )
        processes.append(process)
        return process

    def halt(process):
        stop.touch()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        if process.returncode not in {0, 99}:
            stdout, stderr = process.communicate()
            pytest.fail(f"updater failed with {process.returncode}:\n{stdout}\n{stderr}")

    def status():
        path = control / "status"
        try:
            return path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError):
            # WSL replaces this file atomically; Windows can briefly deny opens
            # during the rename, so let the polling assertion try again.
            return ""

    def commands():
        return command_log.read_text(encoding="utf-8") if command_log.exists() else ""

    def set_clock(timestamp):
        temporary = control / "clock.tmp"
        temporary.write_text(f"{timestamp}\n", encoding="utf-8", newline="\n")
        temporary.replace(control / "clock")

    yield SimpleNamespace(
        control=control, start=start, halt=halt, status=status, commands=commands,
        set_clock=set_clock,
    )
    for process in processes:
        if process.poll() is None:
            halt(process)


@pytest.mark.parametrize("restart", [False, True], ids=["same-process", "after-restart"])
@pytest.mark.parametrize("failure", ["fail-build", "fail-up"])
def test_prepared_update_retries_without_git_network(updater_sandbox, restart, failure):
    sandbox = updater_sandbox
    (sandbox.control / failure).touch()
    process = sandbox.start()
    _wait_until(lambda: sandbox.status() == "failed")
    assert (sandbox.control / "rebuild-revision").read_text().strip() == "new-revision"
    assert sandbox.commands().count("fetch --quiet") == 1
    assert "merge --ff-only FETCH_HEAD" in sandbox.commands()
    if restart:
        sandbox.halt(process)
    (sandbox.control / "offline").touch()
    (sandbox.control / failure).unlink()
    if restart:
        sandbox.start()
    _wait_until(lambda: sandbox.status() == "up_to_date")
    assert sandbox.commands().count("fetch --quiet") == 1
    assert sandbox.commands().count("build --platform linux/amd64") >= 2
    assert sandbox.commands().count("builder=1") >= 2
    assert "up -d --no-deps --force-recreate --wait" in sandbox.commands()
    _wait_until(lambda: not (sandbox.control / "next-remote-check").exists())
    for name in ("needs-rebuild", "next-retry", "rebuild-revision", "next-remote-check"):
        assert not (sandbox.control / name).exists()


@pytest.mark.parametrize("trigger", ["manual", "head-change"])
def test_new_update_does_not_reuse_prepared_revision(updater_sandbox, trigger):
    sandbox = updater_sandbox
    (sandbox.control / "fail-build").touch()
    process = sandbox.start()
    _wait_until(lambda: sandbox.status() == "failed")
    sandbox.halt(process)
    previous_builds = sandbox.commands().count("build --platform linux/amd64")
    (sandbox.control / "offline").touch()
    (sandbox.control / "fail-build").unlink()
    if trigger == "manual":
        (sandbox.control / "request").touch()
    else:
        (sandbox.control / "head").write_text("another-revision\n", encoding="utf-8")
    sandbox.start()
    _wait_until(lambda: sandbox.commands().count("fetch --quiet") >= 2)
    _wait_until(lambda: (sandbox.control / "message").read_text().strip() == "Failed to fetch Git updates")
    assert sandbox.commands().count("build --platform linux/amd64") == previous_builds
    if trigger == "manual":
        assert not (sandbox.control / "rebuild-revision").exists()
    # Once Git is reachable again, normal fast-forward processing resumes.
    (sandbox.control / "offline").unlink()
    _wait_until(lambda: sandbox.status() == "up_to_date")
    assert not (sandbox.control / "rebuild-revision").exists()


@pytest.mark.parametrize("blocker", ["dirty", "non-fast-forward"])
def test_retry_retains_repository_safety_checks(updater_sandbox, blocker):
    sandbox = updater_sandbox
    (sandbox.control / "needs-rebuild").touch()
    (sandbox.control / "rebuild-revision").write_text("old-revision\n", encoding="utf-8")
    (sandbox.control / blocker).touch()
    if blocker == "non-fast-forward":
        (sandbox.control / "request").touch()
    sandbox.start()
    _wait_until(lambda: sandbox.status() == "blocked")
    assert "build --platform linux/amd64" not in sandbox.commands()
    for name in ("needs-rebuild", "rebuild-revision"):
        assert not (sandbox.control / name).exists()


def test_prepared_retry_waits_for_busy_runtime(updater_sandbox):
    sandbox = updater_sandbox
    (sandbox.control / "needs-rebuild").touch()
    (sandbox.control / "rebuild-revision").write_text("old-revision\n", encoding="utf-8")
    (sandbox.control / "runtime-busy").touch()
    (sandbox.control / "offline").touch()
    sandbox.start()
    _wait_until(lambda: sandbox.status() == "deferred")
    assert "fetch --quiet" not in sandbox.commands()
    assert "build --platform linux/amd64" not in sandbox.commands()
    (sandbox.control / "runtime-busy").unlink()
    _wait_until(lambda: sandbox.status() == "up_to_date")
    assert sandbox.commands().count("fetch --quiet") == 1
    assert "build --platform linux/amd64" in sandbox.commands()


@pytest.mark.parametrize("offline_at_check", [False, True], ids=["remote-fixed", "git-offline"])
@pytest.mark.parametrize("transition", ["running", "busy", "restart"])
def test_periodic_check_is_not_postponed_by_failed_retries(
    updater_sandbox, offline_at_check, transition,
):
    sandbox = updater_sandbox
    sandbox.set_clock(1000)
    (sandbox.control / "fail-revision").write_text("new-revision\n", encoding="utf-8")
    process = sandbox.start(interval_seconds="30")
    _wait_until(lambda: sandbox.status() == "failed")
    _wait_until(lambda: (sandbox.control / "next-remote-check").exists())
    assert sandbox.commands().count("fetch --quiet") == 1
    for timestamp, expected_builds in ((1001, 2), (1029, 3)):
        sandbox.set_clock(timestamp)
        _wait_until(lambda: sandbox.commands().count("builder=1") == expected_builds)
        _wait_until(lambda: sandbox.status() == "failed")
        assert sandbox.commands().count("fetch --quiet") == 1
        assert (sandbox.control / "next-remote-check").read_text().strip() == "1030"

    if offline_at_check:
        (sandbox.control / "offline").touch()
        (sandbox.control / "fail-revision").unlink()
    else:
        (sandbox.control / "remote").write_text("fixed-revision\n", encoding="utf-8")
    if transition == "restart":
        sandbox.halt(process)
    elif transition == "busy":
        (sandbox.control / "runtime-busy").touch()
    # Thirty seconds from the original check, not from the last local retry.
    sandbox.set_clock(1030)
    if transition == "restart":
        sandbox.start(interval_seconds="30")
    elif transition == "busy":
        _wait_until(lambda: sandbox.status() == "deferred")
        assert sandbox.commands().count("fetch --quiet") == 1
        assert (sandbox.control / "next-remote-check").read_text().strip() == "1030"
        (sandbox.control / "runtime-busy").unlink()
    _wait_until(lambda: sandbox.status() == "up_to_date")
    assert sandbox.commands().count("fetch --quiet") == 2
    assert sandbox.commands().count("builder=1") == 4
    expected_revision = "new-revision" if offline_at_check else "fixed-revision"
    assert (sandbox.control / "head").read_text().strip() == expected_revision
    assert f"OK_WW_BUILD_REVISION={expected_revision}" in sandbox.commands()
    _wait_until(lambda: not (sandbox.control / "next-remote-check").exists())
    for name in ("needs-rebuild", "next-retry", "rebuild-revision", "next-remote-check"):
        assert not (sandbox.control / name).exists()
