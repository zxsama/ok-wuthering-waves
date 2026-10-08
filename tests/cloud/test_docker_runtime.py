from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from docker import patch_ok_script_wheel
from docker.playwright_adapter import DockerPlaywrightCloudPageAdapter


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (REPOSITORY_ROOT / name).read_text(encoding="utf-8")


def test_linux_runtime_dependencies_are_pinned_and_headless():
    requirements = _read("docker/requirements-cloud.txt")
    package_lines = [
        line for line in requirements.splitlines() if line and not line.startswith("#")
    ]

    assert package_lines
    assert all("==" in line for line in package_lines)
    assert "opencv-python-headless==5.0.0.93" in package_lines
    assert "onnxocr-ppocrv5==0.0.22" in package_lines
    assert "openvino==2026.3.0" in package_lines
    assert not any("pyside" in line.lower() for line in package_lines)
    assert not any(line.startswith("opencv-python==") for line in package_lines)


def test_dockerfile_verifies_compatibility_wheel_and_runtime_imports():
    dockerfile = _read("docker/Dockerfile")

    assert "patch_ok_script_wheel.py" in dockerfile
    assert "python -m pip check" in dockerfile
    assert "python /app/docker/smoke_test.py" in dockerfile
    assert "PYTHONPATH=/app/docker/linux_compat:/app" in dockerfile
    assert "pip install -e" not in dockerfile
    assert dockerfile.index("COPY docker/requirements-cloud.txt") < dockerfile.index(
        "COPY . /app"
    )


def test_default_container_task_runner_is_daily_task():
    expected = "extensions.cloud.task_runner:create_daily_task_runner"

    assert f"OK_WW_CLOUD_TASK_RUNNER: ${{OK_WW_CLOUD_TASK_RUNNER:-{expected}}}" in _read(
        "compose.yaml"
    )
    assert f'OK_WW_CLOUD_TASK_RUNNER:-{expected}' in _read("docker/entrypoint.sh")
    assert f"OK_WW_CLOUD_TASK_RUNNER={expected}" in _read(".env.cloud.example")


def test_compose_has_overridable_container_dns():
    compose = _read("compose.yaml")

    assert "${CLOUD_DNS_PRIMARY:-223.5.5.5}" in compose
    assert "${CLOUD_DNS_SECONDARY:-8.8.8.8}" in compose


def test_compose_does_not_require_an_interactive_terminal():
    compose = _read("compose.yaml")

    assert "stdin_open:" not in compose
    assert "tty:" not in compose


def test_compose_defaults_to_automatic_enrollment_then_web_management():
    compose = _read("compose.yaml")
    environment = _read(".env.cloud.example")
    entrypoint = _read("docker/entrypoint.sh")

    assert "CLOUD_COMMAND: ${CLOUD_COMMAND:-auto}" in compose
    assert 'command_name="${CLOUD_COMMAND:-auto}"' in entrypoint
    assert "CLOUD_COMMAND=auto" in environment
    assert 'if [ "$command_name" = "auto" ]' in entrypoint
    assert '127.0.0.1:${CLOUD_WEB_HOST_PORT:-17880}:${CLOUD_WEB_PORT:-8765}' in compose
    assert (
        '127.0.0.1:${CLOUD_NOVNC_PORT:-15980}:'
        '${CLOUD_NOVNC_INTERNAL_PORT:-15980}'
    ) in compose
    assert "CLOUD_WEB_HOST_PORT=17880" in environment
    assert "CLOUD_NOVNC_PORT=15980" in environment
    assert "CLOUD_NOVNC_INTERNAL_PORT=15980" in environment
    assert '"${CLOUD_NOVNC_INTERNAL_PORT:-15980}" localhost:5900' in entrypoint
    assert ":6080" not in compose
    assert " 6080 localhost:5900" not in entrypoint
    assert 'rm -f "/tmp/.X${display_id}-lock" "/tmp/.X11-unix/X${display_id}"' in entrypoint
    assert '--host "${CLOUD_WEB_HOST:-0.0.0.0}"' in entrypoint
    assert '--port "${CLOUD_WEB_PORT:-8765}"' in entrypoint


def test_image_installs_emoji_font_for_task_icons():
    assert "fonts-noto-color-emoji" in _read("docker/Dockerfile")


def test_image_normalizes_shell_script_line_endings():
    dockerfile = _read("docker/Dockerfile")

    assert "sed -i 's/\\r$//' /app/docker/*.sh" in dockerfile


def test_compose_has_healthcheck_and_bounded_json_logs():
    compose = _read("compose.yaml")

    assert "python /app/docker/runtime_healthcheck.py" in compose
    assert "driver: json-file" in compose
    assert "max-size: ${CLOUD_LOG_MAX_SIZE:-10m}" in compose
    assert 'max-file: "${CLOUD_LOG_MAX_FILES:-5}"' in compose


def test_compose_smoke_checks_container_updates_and_vnc_transport():
    smoke = _read("docker/compose_smoke_test.sh")

    assert "CLOUD_COMMAND=serve docker compose up -d cloud-runner" in smoke
    assert "http://127.0.0.1:17880/api/about" in smoke
    assert '"update_supported":false' in smoke
    assert "http://127.0.0.1:17880/api/updates" in smoke
    assert '"update_available":false' in smoke
    assert "http://127.0.0.1:17880/api/docker-update" in smoke
    assert '"current_version":"v1.0.7"' in smoke
    assert "ws://127.0.0.1:15980/websockify" in smoke
    assert "RFB 003.008" in smoke
    assert "SetProcessDpiAwareness error" in smoke
    assert "calling pyappify.get_version_list" in smoke


def test_runtime_healthcheck_covers_display_vnc_and_management_api():
    healthcheck = _read("docker/runtime_healthcheck.py")

    assert '"xdpyinfo"' in healthcheck
    assert "_check_tcp(5900)" in healthcheck
    assert "_check_tcp(novnc_port)" in healthcheck
    assert 'f"http://127.0.0.1:{web_port}/healthz"' in healthcheck


def test_compose_auto_updater_uses_fast_forward_and_docker_socket():
    compose = _read("compose.yaml")
    updater = _read("docker/auto_update.sh")
    cloud_runner = compose.split("  auto-updater:", 1)[0]

    assert "docker/Dockerfile.updater" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose
    assert ".:/workspace" in compose
    assert "git -C \"$repository_dir\" merge --ff-only FETCH_HEAD" in updater
    assert "status --porcelain --untracked-files=no" in updater
    assert 'autocrlf="${CLOUD_UPDATE_AUTOCRLF:-}"' in updater
    assert 'git -C "$repository_dir" config core.autocrlf "$autocrlf"' in updater
    assert "--project-name \"$project_name\"" in updater
    assert '-f "$repository_dir/docker/Dockerfile"' in updater
    assert '"$image_name" "$repository_dir"' in updater
    assert "--force-recreate --wait" in updater
    assert "CLOUD_UPDATE_POLL_SECONDS" in compose
    assert "update-control:/run/ok-ww-update" in compose
    assert 'if [ -f "$control_dir/request" ]' in updater
    assert 'force_rebuild="1"' in updater
    assert 'write_control needs-rebuild "1"' in updater
    assert 'ps -q cloud-runner' in updater
    assert "'{{.State.Running}}'" in updater
    assert 'rm -f "$marker"' in updater
    assert 'write_status failed "Docker image build failed"' in updater
    assert 'schedule_rebuild_retry' in updater
    assert 'rebuild_retry_due' in updater
    assert 'CLOUD_UPDATE_RETRY_SECONDS' in updater
    assert 'kill "$heartbeat_pid"' in updater
    assert 'rm -f "$control_dir/request.in-progress"' in updater
    assert 'CLOUD_BUILD_VERSION="$build_version"' in updater
    assert "/var/run/docker.sock" not in cloud_runner
    assert ".:/workspace" not in cloud_runner
    assert "CLOUD_UPDATE_CONTROL_DIR: /run/ok-ww-update" in compose
    assert "chown 10001:10001 /run/ok-ww-update" in _read(
        "docker/Dockerfile.updater"
    )


def test_cloud_image_exposes_build_version_without_git_metadata():
    dockerfile = _read("docker/Dockerfile")
    compose = _read("compose.yaml")

    assert "ARG OK_WW_BUILD_VERSION=dev" in dockerfile
    assert "ARG OK_WW_BUILD_REVISION=unknown" in dockerfile
    assert "org.opencontainers.image.version=${OK_WW_BUILD_VERSION}" in dockerfile
    assert "OK_WW_BUILD_VERSION: ${CLOUD_BUILD_VERSION:-dev}" in compose
    assert "OK_WW_BUILD_REVISION: ${CLOUD_BUILD_REVISION:-unknown}" in compose


def test_cloud_image_installs_pinned_web_server_dependencies():
    requirements = _read("docker/requirements-cloud.txt")

    assert "fastapi==0.141.1" in requirements
    assert "uvicorn[standard]==0.52.4" in requirements


def test_container_uses_sandbox_compatible_adapter_without_extra_privileges():
    expected = "docker.playwright_adapter:create_playwright_adapter"

    assert f"OK_WW_CLOUD_ADAPTER: ${{OK_WW_CLOUD_ADAPTER:-{expected}}}" in _read(
        "compose.yaml"
    )
    assert f'OK_WW_CLOUD_ADAPTER:-{expected}' in _read("docker/entrypoint.sh")
    assert f"OK_WW_CLOUD_ADAPTER={expected}" in _read(".env.cloud.example")
    assert "ignore_default_args=()" in _read("docker/playwright_adapter.py")

    adapter = __import__(
        "docker.playwright_adapter", fromlist=["create_playwright_adapter"]
    ).create_playwright_adapter()
    assert adapter.ignore_default_args == ()
    assert {
        "--lang=zh-CN",
        "--kiosk",
        "--start-fullscreen",
        "--window-position=0,0",
        "--window-size=1280,720",
        "--test-type",
        "--disable-infobars",
        "--disable-translate",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--disable-features=Translate,TranslateUI",
    } <= set(adapter.launch_args)


def test_docker_adapter_removes_stale_chrome_singletons(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in DockerPlaywrightCloudPageAdapter._SINGLETON_FILES:
        (profile / name).write_text("stale", encoding="utf-8")
    calls = []
    def record_open(adapter, **kwargs):
        calls.append((kwargs, adapter.launch_args))

    monkeypatch.setattr(
        "extensions.cloud.playwright_adapter.PlaywrightCloudPageAdapter.open",
        record_open,
    )

    adapter = DockerPlaywrightCloudPageAdapter(
        ignore_default_args=(), launch_args=("--kiosk",)
    )
    adapter.open(url="https://example.test", profile_dir=profile, visible=True)

    assert not any((profile / name).exists() for name in adapter._SINGLETON_FILES)
    assert calls == [(
        {"url": "https://example.test", "profile_dir": profile, "visible": True},
        ("--kiosk", "--app=https://example.test"),
    )]
    assert adapter.launch_args == ("--kiosk",)


def test_docker_adapter_marks_profile_exit_normal_without_losing_data(
    tmp_path, monkeypatch
):
    profile = tmp_path / "profile"
    default = profile / "Default"
    default.mkdir(parents=True)
    preferences = default / "Preferences"
    preferences.write_text(
        json.dumps(
            {
                "profile": {"exit_type": "Crashed", "exited_cleanly": False},
                "saved_login_setting": {"opaque": "preserved"},
            }
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        "extensions.cloud.playwright_adapter.PlaywrightCloudPageAdapter.open",
        lambda self, **kwargs: calls.append(kwargs),
    )

    DockerPlaywrightCloudPageAdapter().open(
        url="https://example.test", profile_dir=profile, visible=True
    )

    payload = json.loads(preferences.read_text(encoding="utf-8"))
    assert payload["profile"] == {"exit_type": "Normal", "exited_cleanly": True}
    assert payload["intl"] == {
        "accept_languages": "zh-CN,zh,en-US,en",
        "selected_languages": "zh-CN,zh,en-US,en",
    }
    assert payload["translate"] == {"enabled": False}
    assert payload["translate_blocked_languages"] == ["zh-CN", "zh", "en"]
    assert payload["saved_login_setting"] == {"opaque": "preserved"}
    assert not preferences.with_name("Preferences.ok-ww.tmp").exists()
    assert len(calls) == 1


def test_docker_adapter_creates_safe_preferences_for_new_profile(
    tmp_path, monkeypatch
):
    profile = tmp_path / "new-profile"
    monkeypatch.setattr(
        "extensions.cloud.playwright_adapter.PlaywrightCloudPageAdapter.open",
        lambda self, **kwargs: None,
    )

    DockerPlaywrightCloudPageAdapter().open(
        url="https://example.test", profile_dir=profile, visible=True
    )

    payload = json.loads(
        (profile / "Default" / "Preferences").read_text(encoding="utf-8")
    )
    assert payload["profile"] == {"exit_type": "Normal", "exited_cleanly": True}
    assert payload["translate"] == {"enabled": False}
    assert payload["intl"]["accept_languages"] == "zh-CN,zh,en-US,en"


def test_docker_adapter_marks_last_used_profile_and_tolerates_corrupt_default(
    tmp_path, monkeypatch
):
    profile = tmp_path / "profile"
    selected = profile / "Profile 1"
    selected.mkdir(parents=True)
    (profile / "Default").mkdir()
    (profile / "Default" / "Preferences").write_text("not-json", encoding="utf-8")
    (profile / "Local State").write_text(
        json.dumps({"profile": {"last_used": "Profile 1"}}), encoding="utf-8"
    )
    preferences = selected / "Preferences"
    preferences.write_text(
        json.dumps({"profile": {"exit_type": "Crashed"}, "keep": 1}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "extensions.cloud.playwright_adapter.PlaywrightCloudPageAdapter.open",
        lambda self, **kwargs: None,
    )

    DockerPlaywrightCloudPageAdapter().open(
        url="https://example.test", profile_dir=profile, visible=False
    )

    payload = json.loads(preferences.read_text(encoding="utf-8"))
    assert payload["profile"]["exit_type"] == "Normal"
    assert payload["profile"]["exited_cleanly"] is True
    assert payload["keep"] == 1
    assert (profile / "Default" / "Preferences").read_text() == "not-json"


def test_docker_adapter_rejects_unsafe_last_used_profile_name(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    outside = tmp_path / "Preferences"
    outside.write_text(
        json.dumps({"profile": {"exit_type": "Crashed"}}), encoding="utf-8"
    )
    (profile / "Local State").write_text(
        json.dumps({"profile": {"last_used": "../.."}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        "extensions.cloud.playwright_adapter.PlaywrightCloudPageAdapter.open",
        lambda self, **kwargs: None,
    )

    DockerPlaywrightCloudPageAdapter().open(
        url="https://example.test", profile_dir=profile, visible=False
    )

    assert json.loads(outside.read_text(encoding="utf-8"))["profile"]["exit_type"] == "Crashed"


def test_compatibility_wheel_removes_only_declared_cloud_unused_dependencies(
    tmp_path, monkeypatch
):
    source = tmp_path / patch_ok_script_wheel.EXPECTED_FILENAME
    metadata = b"".join(
        [
            b"Metadata-Version: 2.4\n",
            b"Name: ok-script\n",
            b"Version: 2.0.8\n",
            b"Requires-Dist: requests>=2.32.3\n",
            b"Requires-Dist: pywin32!=312,>=306\n",
            b"Requires-Dist: pydirectinput==1.0.4\n",
            b"Requires-Dist: pycaw==20240210\n",
            b"Requires-Dist: mouse==0.7.1\n",
            b"Requires-Dist: pynput>=1.8.1\n",
            b"\n",
        ]
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(patch_ok_script_wheel.METADATA_PATH, metadata)
        archive.writestr(patch_ok_script_wheel.RECORD_PATH, b"")
        archive.writestr(patch_ok_script_wheel.CAPTURE_INIT_PATH, b"windows imports")
        archive.writestr(patch_ok_script_wheel.CAPTURE_COMPAT_PATH, b"windows imports")
        archive.writestr(
            patch_ok_script_wheel.INTERACTION_INIT_PATH, b"windows imports"
        )
        archive.writestr(
            patch_ok_script_wheel.OK_INIT_PATH,
            b"'windows_graphics_available': "
            b"('ok.util.window', 'windows_graphics_available'),\n"
            b"        try:\n"
            b"            import ctypes\n"
            b"            # Set DPI Awareness (Windows 10 and 8)\n"
            b"            errorCode = ctypes.windll.shcore.SetProcessDpiAwareness(2)\n"
            b"            logger.info(f'SetProcessDpiAwareness {errorCode}')\n"
            b"            if self.debug:\n"
            b"                import win32api\n"
            b"                win32api.SetConsoleCtrlHandler(self.console_handler, True)\n"
            b"        except Exception as e:\n"
            b"            logger.error(f'SetProcessDpiAwareness error', e)\n",
        )
        archive.writestr(
            patch_ok_script_wheel.WINDOW_UTIL_PATH,
            b"import sys\nimport win32api\nimport win32con\nimport win32gui\n"
            b"import win32process\nuser32 = ctypes.WinDLL('user32', use_last_error=True)\n",
        )
        archive.writestr(
            patch_ok_script_wheel.SCREENSHOT_PATH,
            b"            fonts_dir = os.path.join(os.environ['WINDIR'], 'Fonts')\n"
            b"            font = find_first_existing_file(\n"
            b"                ['msyh.ttc', 'msyh.ttf', 'simsun.ttf', 'simsun.ttc', 'arial.ttf', 'arial.ttc'], fonts_dir)\n",
        )
        archive.writestr(
            patch_ok_script_wheel.NOTIFICATION_MANAGER_PATH,
            b"from ok.notification.windows_messenger import MessengerAutomation\n"
            b"        messenger_message = self._messenger_message(title, message)\n",
        )
        archive.writestr(
            patch_ok_script_wheel.PROCESS_UTIL_PATH,
            b"def prevent_sleeping(yes=True):\n"
            b"    # Prevent the system from sleeping\n"
            b"    ctypes.windll.kernel32.SetThreadExecutionState(0x80000002 if yes else 0x80000000)\n",
        )
    monkeypatch.setattr(
        patch_ok_script_wheel,
        "EXPECTED_SHA256",
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )

    patched = patch_ok_script_wheel.patch_wheel(source, tmp_path / "out")

    with zipfile.ZipFile(patched) as archive:
        patched_metadata = archive.read(patch_ok_script_wheel.METADATA_PATH).decode()
        assert archive.testzip() is None
        assert "Requires-Dist: requests>=2.32.3" in patched_metadata
        assert "X-Ok-WW-Compatibility: linux-cloud-1" in patched_metadata
        for dependency in patch_ok_script_wheel.REMOVED_DEPENDENCIES:
            assert f"Requires-Dist: {dependency}" not in patched_metadata
        assert (
            archive.read(patch_ok_script_wheel.CAPTURE_INIT_PATH)
            == patch_ok_script_wheel.LINUX_CAPTURE_INIT
        )
        assert (
            archive.read(patch_ok_script_wheel.CAPTURE_COMPAT_PATH)
            == patch_ok_script_wheel.LINUX_CAPTURE_COMPAT
        )
        assert (
            archive.read(patch_ok_script_wheel.INTERACTION_INIT_PATH)
            == patch_ok_script_wheel.LINUX_INTERACTION_INIT
        )
        assert (
            b"('ok.linux_cloud_compat', 'windows_graphics_available')"
            in archive.read(patch_ok_script_wheel.OK_INIT_PATH)
        )
        assert b"if sys.platform == 'win32':" in archive.read(
            patch_ok_script_wheel.OK_INIT_PATH
        )
        assert (
            archive.read(patch_ok_script_wheel.LINUX_COMPAT_PATH)
            == patch_ok_script_wheel.LINUX_COMPAT
        )
        patched_window = archive.read(patch_ok_script_wheel.WINDOW_UTIL_PATH)
        assert b'if sys.platform == "win32"' in patched_window
        assert b'else None' in patched_window
        patched_screenshot = archive.read(patch_ok_script_wheel.SCREENSHOT_PATH)
        assert b"NotoSansCJK-Regular.ttc" in patched_screenshot
        assert b"os.environ['WINDIR']" in patched_screenshot
        patched_notifications = archive.read(
            patch_ok_script_wheel.NOTIFICATION_MANAGER_PATH
        )
        assert not patched_notifications.startswith(
            b"from ok.notification.windows_messenger import MessengerAutomation"
        )
        assert b"from ok.notification.windows_messenger import MessengerAutomation" in patched_notifications
        patched_process = archive.read(patch_ok_script_wheel.PROCESS_UTIL_PATH)
        assert b"if sys.platform != 'win32':" in patched_process


def test_compatibility_wheel_rejects_unverified_upstream_artifact(tmp_path):
    source = tmp_path / patch_ok_script_wheel.EXPECTED_FILENAME
    source.write_bytes(b"not the pinned wheel")

    with pytest.raises(RuntimeError, match="unexpected .* sha256"):
        patch_ok_script_wheel.patch_wheel(source, tmp_path / "out")
