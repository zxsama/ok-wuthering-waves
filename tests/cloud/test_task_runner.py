from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from extensions.cloud.models import CloudConfigurationError
from extensions.cloud.task_runner import (
    DEFAULT_TASK_TIMEOUT_SECONDS,
    TASK_CONFIG_DIR_ENV,
    CloudTaskError,
    _cleanup_runtime,
    _task_timeout,
    build_cloud_task_config,
    create_daily_task_runner,
    create_task_runner,
    run_ok_task,
)


def test_build_cloud_task_config_is_peripheral_and_does_not_mutate_source(monkeypatch):
    monkeypatch.delenv(TASK_CONFIG_DIR_ENV, raising=False)
    source = {
        "gui": {"type": "qt"},
        "config_folder": "configs",
        "windows": {"exe": "game.exe", "start_exe": True},
        "analytics": True,
    }

    result = build_cloud_task_config(source)

    assert source["windows"]["start_exe"] is True
    assert "gui" not in result
    assert result["windows"] == {"exe": "game.exe", "start_exe": False}
    assert result["check_mutex"] is False
    assert result["analytics"] is False
    assert result["use_overlay"] is False
    assert result["config_folder"] == "configs"


def test_build_cloud_task_config_can_use_persistent_external_config(monkeypatch):
    monkeypatch.setenv(TASK_CONFIG_DIR_ENV, "/data/cloud/configs")

    result = build_cloud_task_config({"config_folder": "configs"})

    assert result["config_folder"] == "/data/cloud/configs"


def test_daily_factory_returns_adapter_aware_runner(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "extensions.cloud.task_runner.run_ok_task",
        lambda adapter, task, timeout_seconds: calls.append((adapter, task, timeout_seconds)),
    )
    adapter = object()

    create_daily_task_runner()(adapter)

    assert calls == [(adapter, "DailyTask", None)]


def test_generic_factory_can_bind_another_onetime_task(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "extensions.cloud.task_runner.run_ok_task",
        lambda adapter, task, timeout_seconds: calls.append((adapter, task, timeout_seconds)),
    )
    adapter = object()

    create_task_runner("DiagnosisTask")(adapter)

    assert calls == [(adapter, "DiagnosisTask", None)]


def test_generic_factory_binds_explicit_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "extensions.cloud.task_runner.run_ok_task",
        lambda adapter, task, timeout_seconds: calls.append(
            (adapter, task, timeout_seconds)
        ),
    )

    create_task_runner("DiagnosisTask", timeout_seconds=12)(object())

    assert calls[0][1:] == ("DiagnosisTask", 12)


def test_task_timeout_uses_environment(monkeypatch):
    monkeypatch.setenv("OK_WW_CLOUD_TASK_TIMEOUT", "123.5")
    assert _task_timeout() == 123.5


def test_task_timeout_defaults_and_rejects_invalid_values(monkeypatch):
    monkeypatch.delenv("OK_WW_CLOUD_TASK_TIMEOUT", raising=False)
    assert _task_timeout() == DEFAULT_TASK_TIMEOUT_SECONDS
    with pytest.raises(CloudConfigurationError, match="must be positive"):
        _task_timeout(0)


def test_cleanup_releases_joins_and_closes_without_raising(caplog):
    calls = []

    class AliveThread:
        def is_alive(self):
            return True

        def join(self, timeout):
            calls.append(("join", timeout))

    class Interaction:
        def release_all(self):
            calls.append("release")
            raise RuntimeError("release failed")

    class Manager:
        interaction = Interaction()

        def close(self):
            calls.append("close")

    class ExitEvent:
        def set(self):
            calls.append("exit")

    class OkInstance:
        exit_event = ExitEvent()
        task_executor = type("Executor", (), {"thread": AliveThread()})()

        def quit(self):
            calls.append("quit")

    _cleanup_runtime(OkInstance(), Manager(), None)

    assert calls == ["exit", "quit", "release", ("join", 10.0), "close"]
    assert "input release" in caplog.text


def test_run_ok_task_times_out_then_signals_and_cleans_up(monkeypatch):
    calls = []

    class Signal:
        def connect(self, callback):
            calls.append("connect")

        def disconnect(self, callback):
            calls.append("disconnect")

    class Manager:
        interaction = SimpleNamespace(release_all=lambda: calls.append("release"))

        def __init__(self, adapter):
            calls.append(("manager", adapter))

        def close(self):
            calls.append("close")

    class FakeOK:
        def __init__(self, config):
            self.exit_event = threading.Event()
            self.task_executor = SimpleNamespace(thread=None)
            calls.append(("config", config))

        def get_task(self, task):
            return SimpleNamespace(info_get=lambda key: None), False

        def run_onetime_task(self, task, exit_after=False):
            self.exit_event.wait(1)

        def quit(self):
            calls.append("quit")
            self.exit_event.set()

    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(config={}))
    monkeypatch.setitem(
        sys.modules,
        "ok.core.events",
        SimpleNamespace(communicate=SimpleNamespace(task_done=Signal())),
    )
    monkeypatch.setattr("extensions.cloud.task_runner.CloudDeviceManager", Manager)
    monkeypatch.setattr(
        "extensions.cloud.task_runner.create_cloud_ok_class", lambda manager: FakeOK
    )

    with pytest.raises(CloudTaskError, match="timed out after 0.01 seconds"):
        run_ok_task("adapter", timeout_seconds=0.01)

    assert calls == [
        ("manager", "adapter"),
        ("config", {"use_gui": False, "use_overlay": False, "check_mutex": False,
                    "analytics": False, "windows": {"start_exe": False}}),
        "connect",
        "disconnect",
        "quit",
        "release",
        "close",
    ]


def test_cleanup_failure_does_not_replace_task_exception(monkeypatch):
    class Signal:
        def connect(self, callback):
            pass

        def disconnect(self, callback):
            raise RuntimeError("disconnect failed")

    class Manager:
        interaction = SimpleNamespace(release_all=lambda: None)

        def __init__(self, adapter):
            pass

        def close(self):
            raise RuntimeError("close failed")

    class FakeOK:
        exit_event = threading.Event()
        task_executor = SimpleNamespace(thread=None)

        def __init__(self, config):
            pass

        def get_task(self, task):
            return object(), False

        def run_onetime_task(self, task, exit_after=False):
            raise ValueError("primary task failure")

        def quit(self):
            raise RuntimeError("quit failed")

    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(config={}))
    monkeypatch.setitem(
        sys.modules,
        "ok.core.events",
        SimpleNamespace(communicate=SimpleNamespace(task_done=Signal())),
    )
    monkeypatch.setattr("extensions.cloud.task_runner.CloudDeviceManager", Manager)
    monkeypatch.setattr(
        "extensions.cloud.task_runner.create_cloud_ok_class", lambda manager: FakeOK
    )

    with pytest.raises(ValueError, match="primary task failure"):
        run_ok_task("adapter", timeout_seconds=1)


def test_task_errors_are_cloud_runner_errors():
    assert issubclass(CloudTaskError, RuntimeError)
    assert issubclass(CloudConfigurationError, RuntimeError)
