"""Default bridge from the cloud session to the repository's DailyTask."""

from __future__ import annotations

import copy
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from .models import CloudConfigurationError, CloudRunnerError
from .ok_bridge import CloudDeviceManager, create_cloud_ok_class


DEFAULT_TASK = "DailyTask"
DEFAULT_TASK_TIMEOUT_SECONDS = 3600.0
DEFAULT_CLEANUP_TIMEOUT_SECONDS = 10.0
TASK_TIMEOUT_ENV = "OK_WW_CLOUD_TASK_TIMEOUT"
TASK_CONFIG_DIR_ENV = "OK_WW_CLOUD_CONFIG_DIR"

logger = logging.getLogger(__name__)


class CloudTaskError(CloudRunnerError):
    """Raised when an ok-script task did not reach its success event."""


def build_cloud_task_config(source: dict[str, Any]) -> dict[str, Any]:
    """Copy the upstream config and disable native-window ownership."""

    config = copy.deepcopy(source)
    config.pop("gui", None)
    config["use_gui"] = False
    config["use_overlay"] = False
    config["check_mutex"] = False
    config["analytics"] = False
    config_dir = os.environ.get(TASK_CONFIG_DIR_ENV, "").strip()
    if config_dir:
        config["config_folder"] = config_dir
    windows = dict(config.get("windows") or {})
    windows["start_exe"] = False
    config["windows"] = windows
    return config


def _task_timeout(value: float | None = None) -> float:
    raw_value: Any = value if value is not None else os.environ.get(TASK_TIMEOUT_ENV)
    if raw_value in (None, ""):
        return DEFAULT_TASK_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise CloudConfigurationError(f"{TASK_TIMEOUT_ENV} must be a number") from exc
    if timeout <= 0:
        raise CloudConfigurationError(f"{TASK_TIMEOUT_ENV} must be positive")
    return timeout


def _cleanup_runtime(
    ok_instance: Any,
    manager: CloudDeviceManager,
    task_thread: threading.Thread | None,
) -> None:
    """Best-effort bounded cleanup which never replaces a task failure."""

    def attempt(label: str, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except BaseException:
            logger.exception("cloud task cleanup failed during %s", label)

    if ok_instance is not None:
        exit_event = getattr(ok_instance, "exit_event", None)
        if exit_event is not None:
            attempt("exit signal", exit_event.set)
        attempt("ok quit", ok_instance.quit)

    interaction = getattr(manager, "interaction", None)
    if interaction is not None:
        attempt("input release", interaction.release_all)

    executor = getattr(ok_instance, "task_executor", None)
    executor_thread = getattr(executor, "thread", None)
    if (
        executor_thread is not None
        and executor_thread is not threading.current_thread()
        and executor_thread.is_alive()
    ):
        attempt(
            "executor join",
            lambda: executor_thread.join(timeout=DEFAULT_CLEANUP_TIMEOUT_SECONDS),
        )
        if executor_thread.is_alive():
            logger.warning("ok-script executor did not stop within cleanup timeout")

    if (
        task_thread is not None
        and task_thread is not threading.current_thread()
        and task_thread.is_alive()
    ):
        attempt(
            "task runner join",
            lambda: task_thread.join(timeout=DEFAULT_CLEANUP_TIMEOUT_SECONDS),
        )
        if task_thread.is_alive():
            logger.warning("cloud task runner did not stop within cleanup timeout")

    attempt("device manager close", manager.close)


def run_ok_task(
    adapter: Any,
    task: Any = DEFAULT_TASK,
    *,
    timeout_seconds: float | None = None,
) -> None:
    """Run one configured ok-script task against the active Playwright page."""

    communicate = None
    connected = False
    try:
        from config import config as project_config
        from ok.core.events import communicate
    except ImportError as exc:
        raise CloudConfigurationError(
            "the DailyTask runner requires this repository's ok-script dependencies"
        ) from exc

    manager = CloudDeviceManager(adapter)
    CloudOK = create_cloud_ok_class(manager)
    ok_instance = None
    completed = threading.Event()
    invocation_finished = threading.Event()
    invocation_errors: list[BaseException] = []
    task_thread: threading.Thread | None = None
    selected_task = None
    timeout = _task_timeout(timeout_seconds)

    def record_completion(finished_task: Any) -> None:
        if finished_task is selected_task:
            completed.set()

    try:
        ok_instance = CloudOK(build_cloud_task_config(project_config))
        selected_task, is_trigger = ok_instance.get_task(task)
        if is_trigger:
            raise CloudConfigurationError(
                "the cloud one-shot runner only accepts one-time tasks"
            )
        communicate.task_done.connect(record_completion)
        connected = True
        def invoke_task() -> None:
            try:
                ok_instance.run_onetime_task(selected_task, exit_after=False)
            except BaseException as exc:
                invocation_errors.append(exc)
            finally:
                invocation_finished.set()

        task_thread = threading.Thread(
            target=invoke_task,
            name="CloudTaskRunner",
            daemon=True,
        )
        task_thread.start()
        if not invocation_finished.wait(timeout):
            raise CloudTaskError(
                f"ok-script task timed out after {timeout:g} seconds"
            )
        if invocation_errors:
            raise invocation_errors[0]
        if not completed.is_set():
            detail = selected_task.info_get("Error") if selected_task is not None else None
            suffix = f": {detail}" if detail else ""
            raise CloudTaskError(
                f"ok-script task did not report successful completion{suffix}"
            )
    finally:
        if connected and communicate is not None:
            try:
                communicate.task_done.disconnect(record_completion)
            except BaseException:
                logger.exception("cloud task cleanup failed while disconnecting signal")
        _cleanup_runtime(ok_instance, manager, task_thread)


def create_task_runner(
    task: Any = DEFAULT_TASK,
    *,
    timeout_seconds: float | None = None,
) -> Callable[[Any], None]:
    """Return a coordinator-compatible runner bound to an ok-script task."""

    def run(adapter: Any) -> None:
        run_ok_task(adapter, task=task, timeout_seconds=timeout_seconds)

    return run


def create_daily_task_runner() -> Callable[[Any], None]:
    """CLI factory for the repository's standard DailyTask flow."""

    return create_task_runner(DEFAULT_TASK)
