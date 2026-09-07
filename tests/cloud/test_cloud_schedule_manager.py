from __future__ import annotations

import time
from datetime import UTC, datetime

from ok.util.windows_schedule import ScheduleTaskInfo, TriggerType

from extensions.cloud.cloud_schedule_manager import CloudScheduleManager
from extensions.cloud.cloud_schedules import DEFAULT_DAILY_TASK_ID, DEFAULT_SCHEDULE_ID


def make_manager(tmp_path, queued=None, **kwargs):
    return CloudScheduleManager(
        tmp_path / "schedules.json",
        (queued if queued is not None else []).append,
        now=kwargs.pop("now", lambda: datetime(2026, 9, 6, 0, 0, tzinfo=UTC)),
        **kwargs,
    )


def test_default_daily_task_is_exposed_as_web_schedule_info(tmp_path):
    manager = make_manager(tmp_path)

    tasks = manager.query_all_tasks(force_sync=True)

    assert len(tasks) == 1
    task = tasks[0]
    assert isinstance(task, ScheduleTaskInfo)
    assert task.path == rf"\Cloud\{DEFAULT_SCHEDULE_ID}"
    assert task.task_identifier == DEFAULT_DAILY_TASK_ID
    assert task.trigger_type == "Daily"
    assert task.start_hour == 4
    assert task.start_minute == 0
    assert task.enabled is True
    assert manager.cache.get(task.path) is task
    assert manager.cache.get(task.name) is task
    assert manager.cache.values() == tasks


def test_web_crud_enable_disable_and_replace(tmp_path):
    manager = make_manager(tmp_path)
    assert manager.create_task(
        "Forgery Challenge",
        3,
        TriggerType.WEEKLY,
        timeout_hours=2,
        start_hour=7,
        start_minute=35,
        auto_exit=False,
        email_report=False,
        task_identifier="src.task.ForgeryTask.ForgeryTask",
    )
    created = manager.cache.get("Forgery Challenge")
    assert created.task_identifier == "src.task.ForgeryTask.ForgeryTask"
    assert created.trigger_type == "Weekly"
    assert (created.start_hour, created.start_minute) == (7, 35)
    assert created.timeout_hours == 2
    assert created.auto_exit is False
    assert created.email_report is False

    assert manager.disable_task(created.path)
    assert manager.cache.get(created.path).enabled is False
    assert manager.enable_task(created.path)
    assert manager.cache.get(created.path).enabled is True

    assert manager.replace_task(
        created.path,
        3,
        TriggerType.CUSTOM,
        timeout_hours=4,
        start_hour=8,
        start_minute=10,
        auto_exit=True,
        email_report=True,
        interval_days=2,
        interval_hours=0,
        task_identifier="src.task.ForgeryTask.ForgeryTask",
    )
    replaced = manager.cache.get(created.path)
    assert replaced.trigger_type == "Custom"
    assert replaced.interval_days == 2
    assert replaced.interval_hours == 0
    assert replaced.email_report is True
    assert replaced.timeout_hours == 4

    assert manager.delete_task(created.path)
    assert manager.cache.get(created.path) is None


def test_supports_monthly_once_and_hourly_custom(tmp_path):
    manager = make_manager(tmp_path)
    for name, trigger, days, hours in (
        ("Monthly Task", TriggerType.MONTHLY, 0, 0),
        ("Once Task", TriggerType.ONCE, 0, 0),
        ("Hourly Task", TriggerType.CUSTOM, 0, 3),
    ):
        assert manager.create_task(
            name,
            2,
            trigger,
            start_hour=9,
            start_minute=15,
            interval_days=days,
            interval_hours=hours,
            task_identifier=f"src.task.{name.replace(' ', '')}.{name.replace(' ', '')}",
        )
    values = {task.name: task for task in manager.query_all_tasks(force_sync=True)}
    assert values["Monthly Task"].trigger_type == "Monthly"
    assert values["Once Task"].trigger_type == "Once"
    assert values["Hourly Task"].interval_hours == 3
    assert all(task.next_run_time for task in values.values())


def test_due_schedule_enqueues_stable_task_request(tmp_path):
    queued = []
    manager = CloudScheduleManager(tmp_path / "schedules.json", queued.append)

    requests = manager.run_pending(datetime(2026, 9, 5, 20, 0, tzinfo=UTC))

    assert len(requests) == 1
    assert queued == list(requests)
    assert requests[0].task_id == DEFAULT_DAILY_TASK_ID


def test_custom_hour_schedule_can_run_more_than_once_per_day(tmp_path):
    queued = []
    now = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)
    manager = make_manager(tmp_path, queued, now=lambda: now)
    assert manager.create_task(
        "Frequent Task",
        4,
        TriggerType.CUSTOM,
        start_hour=8,
        start_minute=0,
        interval_hours=2,
        task_identifier="src.task.FrequentTask.FrequentTask",
    )
    schedule = manager.cache.get("Frequent Task")
    assert manager.run_pending(datetime(2026, 9, 6, 2, 0, tzinfo=UTC))
    assert manager.run_pending(datetime(2026, 9, 6, 4, 0, tzinfo=UTC))
    frequent = [request for request in queued if request.schedule_id in schedule.path]
    assert len(frequent) == 2


def test_start_stop_background_dispatcher_is_idempotent(tmp_path):
    manager = make_manager(tmp_path, poll_interval=0.01)

    manager.start()
    thread = manager.sync_thread
    manager.start()
    assert manager.sync_thread is thread
    time.sleep(0.03)
    manager.stop()

    assert manager.running is False
    assert manager.sync_thread is None
    assert thread is not None and not thread.is_alive()


def test_invalid_web_requests_return_false(tmp_path):
    manager = make_manager(tmp_path)
    assert not manager.create_task(
        "No stable id", 1, TriggerType.DAILY, task_identifier=None
    )
    assert not manager.create_task(
        "Bad time", 1, TriggerType.DAILY, start_hour=25,
        task_identifier="src.task.Bad.Bad",
    )
    assert not manager.delete_task("missing")
    assert not manager.enable_task("missing")
    assert not manager.disable_task("missing")
