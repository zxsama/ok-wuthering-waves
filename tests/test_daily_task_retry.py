import importlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ok.core.config_schema import build_config_fields
from ok.task.exceptions import CaptureException, FinishedException, TaskDisabledException
from ok.task.TaskExecutor import TaskExecutor
from ok.util.config import Config

from extensions.daily_task_retry import (
    DEFAULT_RETRY_COUNT,
    RETRY_COUNT,
    RETRY_DELAY_SECONDS,
    install_daily_task_retry,
)
from src.task.DailyTask import DailyTask


def make_task(retries=None):
    task = SimpleNamespace(
        default_config={}, config_description={}, config_type={}, config={},
        validate_config=Mock(return_value=None), run=Mock(),
        exit_is_set=Mock(return_value=False), enabled=True,
        log_warning=Mock(), sleep=Mock(), sleep_check_interval=0.4,
    )
    if retries is not None:
        task.config[RETRY_COUNT] = retries
    original_run = task.run
    install_daily_task_retry(task)
    return task, original_run


@pytest.mark.parametrize("retries, calls", [(None, 6), (0, 1), (2, 3)])
def test_exhaustion_preserves_last_exception_and_exact_retry_count(retries, calls):
    task, original = make_task(retries)
    failures = [RuntimeError(f"failure {i}") for i in range(calls)]
    original.side_effect = failures

    with pytest.raises(RuntimeError) as caught:
        task.run()

    assert caught.value is failures[-1]
    assert original.call_count == calls
    assert task.sleep.call_count == calls - 1
    assert task.log_warning.call_count == calls - 1
    assert all(call.args == (RETRY_DELAY_SECONDS,) for call in task.sleep.call_args_list)


@pytest.mark.parametrize("result", [None, False, "done"])
def test_success_preserves_return_value_and_arguments(result):
    task, original = make_task()
    original.side_effect = [RuntimeError("temporary"), result]

    assert task.run("argument", option=True) is result

    assert original.call_count == 2
    assert all(call.args == ("argument",) and call.kwargs == {"option": True}
               for call in original.call_args_list)
    task.sleep.assert_called_once_with(RETRY_DELAY_SECONDS)


@pytest.mark.parametrize("exception_type", [
    TaskDisabledException, FinishedException, KeyboardInterrupt, SystemExit,
])
def test_stop_and_exit_exceptions_are_never_retried(exception_type):
    task, original = make_task()
    failure = exception_type()
    original.side_effect = failure

    with pytest.raises(exception_type) as caught:
        task.run()

    assert caught.value is failure
    original.assert_called_once()
    task.sleep.assert_not_called()


@pytest.mark.parametrize("stage", ["failure", "wait"])
@pytest.mark.parametrize("stop_kind", ["disabled", "exit"])
def test_stop_state_prevents_another_attempt(stage, stop_kind):
    task, original = make_task()

    def stop():
        if stop_kind == "disabled":
            task.enabled = False
        else:
            task.exit_is_set.return_value = True

    def fail():
        if stage == "failure":
            stop()
        raise RuntimeError("temporary")

    original.side_effect = fail
    task.sleep.side_effect = lambda seconds: stop()

    with pytest.raises(TaskDisabledException if stop_kind == "disabled" else FinishedException):
        task.run()

    original.assert_called_once()
    assert task.sleep.call_count == (stage == "wait")


def test_wait_cancellation_propagates_without_another_attempt():
    task, original = make_task()
    original.side_effect = RuntimeError("temporary")
    cancelled = TaskDisabledException()
    task.sleep.side_effect = cancelled

    with pytest.raises(TaskDisabledException) as caught:
        task.run()

    assert caught.value is cancelled
    original.assert_called_once()
    assert task.sleep_check_interval == 0.4


def test_budget_resets_per_invocation_and_installation_is_idempotent():
    task, original = make_task(1)
    wrapped = task.run
    install_daily_task_retry(task)
    assert task.run is wrapped
    original.side_effect = [RuntimeError("first"), "first done", RuntimeError("second"), "second done"]

    assert task.run() == "first done"
    assert task.run() == "second done"
    assert original.call_count == 4


@pytest.mark.parametrize("value", [-1, True, False, 1.5, "2", None])
def test_invalid_config_is_rejected_before_running(value):
    task, original = make_task()
    task.config[RETRY_COUNT] = value

    assert task.validate_config(RETRY_COUNT, value)
    with pytest.raises(ValueError, match="non-negative integer"):
        task.run()
    original.assert_not_called()


def test_existing_validation_is_preserved():
    task, _ = make_task()
    original_validate = task.validate_config.__wrapped__
    original_validate.return_value = "existing validation"

    assert task.validate_config("Existing Setting", "value") == "existing validation"
    original_validate.assert_called_once_with("Existing Setting", "value")


def create_daily_task(executor=None):
    executor = executor or SimpleNamespace(
        scene=None, text_fix={}, exit_event=threading.Event(),
        global_config=SimpleNamespace(get_config=lambda name: {}),
    )
    return DailyTask(executor=executor, app=SimpleNamespace(tr=lambda text: text))


def make_executor_retry_task(monkeypatch):
    """Use the real task/executor sleep loop, replacing only elapsed time and I/O."""
    clock = SimpleNamespace(now=100.0)
    executor_module = importlib.import_module("ok.task.TaskExecutor")
    monkeypatch.setattr(executor_module, "time", SimpleNamespace(time=lambda: clock.now))
    executor = TaskExecutor.__new__(TaskExecutor)
    executor.scene = None
    executor.text_fix = {}
    executor.exit_event = threading.Event()
    executor.global_config = SimpleNamespace(get_config=lambda name: {})
    executor.debug_mode = False
    executor.paused = False
    executor.device_manager = SimpleNamespace(interaction=SimpleNamespace(should_capture=lambda: True))
    original = Mock(side_effect=RuntimeError("temporary task failure"))
    monkeypatch.setattr(DailyTask, "run", original)
    task = create_daily_task(executor)
    task.config = dict(task.default_config)
    task._enabled = True
    task.sleep_check_interval = 0.4
    task.log_warning = Mock()
    task.sleep_check = Mock(side_effect=RuntimeError("temporary task check failure"))
    executor.current_task = task
    executor.next_frame = Mock(side_effect=CaptureException("temporary frame failure"))

    def advance(seconds):
        clock.now += seconds

    executor._wait_for_activity = Mock(side_effect=advance)
    return task, original, executor, clock


@pytest.mark.parametrize("failure_stage", ["capture", "task_check"])
def test_real_executor_retry_delay_skips_capture_and_task_checks(monkeypatch, failure_stage):
    task, original, executor, _ = make_executor_retry_task(monkeypatch)
    if failure_stage == "task_check":
        executor.next_frame.side_effect = None
    failure = RuntimeError("last task failure")
    original.side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        task.run()

    assert caught.value is failure
    assert original.call_count == 6
    assert executor._wait_for_activity.call_count == 5
    executor.next_frame.assert_not_called()
    task.sleep_check.assert_not_called()
    assert task.sleep_check_interval == 0.4
    # Normal task sleeps must still execute their configured checks afterward.
    with pytest.raises(CaptureException if failure_stage == "capture" else RuntimeError):
        task.sleep(1)


@pytest.mark.parametrize("stop_kind", ["disabled", "exit"])
def test_real_executor_retry_delay_stops_and_restores_checks(monkeypatch, stop_kind):
    task, original, executor, _ = make_executor_retry_task(monkeypatch)

    def stop(seconds):
        if stop_kind == "disabled":
            task._enabled = False
        else:
            executor.exit_event.set()

    executor._wait_for_activity.side_effect = stop

    with pytest.raises(TaskDisabledException if stop_kind == "disabled" else SystemExit):
        task.run()

    original.assert_called_once()
    assert task.sleep_check_interval == 0.4
    executor.next_frame.assert_not_called()
    task.sleep_check.assert_not_called()


@pytest.mark.parametrize("pause_target", ["executor", "task"])
def test_real_executor_retry_delay_respects_pause(monkeypatch, pause_target):
    task, original, executor, clock = make_executor_retry_task(monkeypatch)
    original.side_effect = [RuntimeError("temporary"), "done"]
    if pause_target == "executor":
        executor.paused = True
    else:
        task._paused = True
    waits = []

    def resume(seconds):
        waits.append(seconds)
        assert original.call_count == 1
        clock.now += seconds
        executor.paused = False
        task._paused = False

    executor._wait_for_activity.side_effect = resume

    assert task.run() == "done"

    assert waits[0] == 0.1
    assert sum(waits) == pytest.approx(RETRY_DELAY_SECONDS)
    assert task.sleep_check_interval == 0.4
    executor.next_frame.assert_not_called()
    task.sleep_check.assert_not_called()


def test_daily_task_installs_before_config_load_and_setting_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "config_folder", str(tmp_path))
    first = create_daily_task()
    assert first.config is None
    assert first.default_config[RETRY_COUNT] == DEFAULT_RETRY_COUNT
    first.load_config()
    assert first.config[RETRY_COUNT] == DEFAULT_RETRY_COUNT
    first.config[RETRY_COUNT] = 2

    second = create_daily_task()
    second.load_config()

    assert type(second) is DailyTask
    assert second.config[RETRY_COUNT] == 2
    assert json.loads((tmp_path / "DailyTask.json").read_text(encoding="utf-8"))[RETRY_COUNT] == 2


@pytest.mark.parametrize("saved_value", ["missing", -1, True, 1.5, "2"])
def test_old_or_invalid_saved_config_gets_default_without_losing_other_values(
        tmp_path, monkeypatch, saved_value):
    monkeypatch.setattr(Config, "config_folder", str(tmp_path))
    saved = {"Which Tacet Suppression to Farm": 3}
    if saved_value != "missing":
        saved[RETRY_COUNT] = saved_value
    (tmp_path / "DailyTask.json").write_text(json.dumps(saved), encoding="utf-8")

    task = create_daily_task()
    task.load_config()

    assert task.config[RETRY_COUNT] == DEFAULT_RETRY_COUNT
    assert task.config["Which Tacet Suppression to Farm"] == 3


def test_retry_setting_is_exposed_as_integer_in_actual_web_config_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "config_folder", str(tmp_path))
    task = create_daily_task()
    task.load_config()

    fields = build_config_fields(
        task.config, task.config_description, task.config_type, task.default_config,
    )
    field = next(field for field in fields if field["key"] == RETRY_COUNT)

    assert field["kind"] == "integer"
    assert field["value"] == field["default"] == 5
    assert field["minimum"] == 0
    assert "0 disables retries" in field["description"]
