import json
from datetime import UTC, datetime

import pytest

from extensions.cloud.cloud_schedules import (
    DEFAULT_DAILY_TASK_ID,
    DEFAULT_SCHEDULE_ID,
    CloudScheduleDispatcher,
    CloudScheduleStore,
    DuplicateScheduleError,
    ScheduleNotFoundError,
)
from extensions.cloud.models import CloudConfigurationError


def make_store(tmp_path):
    return CloudScheduleStore(tmp_path / "schedules.json")


def test_first_start_persists_enabled_daily_task_default(tmp_path):
    path = tmp_path / "schedules.json"
    store = CloudScheduleStore(path)

    assert store.list() == (store.get(DEFAULT_SCHEDULE_ID),)
    schedule = store.get(DEFAULT_SCHEDULE_ID)
    assert schedule.task_id == DEFAULT_DAILY_TASK_ID
    assert schedule.run_at == "04:00"
    assert schedule.timezone == "Asia/Shanghai"
    assert schedule.missed_window_minutes == 120
    assert schedule.enabled is True

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["version"] == 1
    assert persisted["schedules"] == [
        {
            "id": DEFAULT_SCHEDULE_ID,
            "task_id": "src.task.DailyTask.DailyTask",
            "run_at": "04:00",
            "timezone": "Asia/Shanghai",
            "missed_window_minutes": 120,
            "enabled": True,
        }
    ]


def test_existing_empty_configuration_does_not_recreate_default(tmp_path):
    path = tmp_path / "schedules.json"
    first = CloudScheduleStore(path)
    first.delete(DEFAULT_SCHEDULE_ID)

    assert CloudScheduleStore(path).list() == ()


def test_crud_and_enable_disable_any_task(tmp_path):
    store = make_store(tmp_path)
    created = store.create(
        schedule_id="weekly-garden",
        task_id="src.task.GardenTask.GardenTask",
        run_at="07:30",
        timezone="Asia/Tokyo",
        missed_window_minutes=45,
    )

    assert store.get(created.id) == created
    assert created.email_report is True
    assert created in store.list()
    disabled = store.set_enabled(created.id, False)
    assert disabled.enabled is False
    updated = store.update(
        created.id,
        task_id="src.task.TacetTask.TacetTask",
        run_at="08:15",
        timezone="Europe/London",
        missed_window_minutes=60,
        enabled=True,
    )
    assert updated.task_id == "src.task.TacetTask.TacetTask"
    assert updated.run_at == "08:15"
    assert updated.timezone == "Europe/London"
    assert updated.missed_window_minutes == 60
    assert updated.enabled is True

    store.delete(created.id)
    with pytest.raises(ScheduleNotFoundError):
        store.get(created.id)


def test_create_rejects_duplicates_and_invalid_fields(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(DuplicateScheduleError):
        store.create(
            schedule_id=DEFAULT_SCHEDULE_ID,
            task_id=DEFAULT_DAILY_TASK_ID,
            run_at="05:00",
            timezone="Asia/Shanghai",
            missed_window_minutes=1,
        )
    invalid_values = [
        {"task_id": "DailyTask"},
        {"run_at": "4:00"},
        {"timezone": "Mars/Base"},
        {"missed_window_minutes": 0},
    ]
    base = {
        "task_id": "src.task.TacetTask.TacetTask",
        "run_at": "05:00",
        "timezone": "Asia/Shanghai",
        "missed_window_minutes": 30,
    }
    for index, change in enumerate(invalid_values):
        with pytest.raises(CloudConfigurationError):
            store.create(schedule_id=f"invalid-{index}", **(base | change))


def test_save_uses_atomic_replace_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    replacements = []
    from extensions.cloud import cloud_schedules

    real_replace = cloud_schedules.os.replace

    def observe_replace(source, destination):
        replacements.append((source, destination))
        assert source.exists()
        return real_replace(source, destination)

    monkeypatch.setattr(cloud_schedules.os, "replace", observe_replace)
    store.set_enabled(DEFAULT_SCHEDULE_ID, False)

    assert len(replacements) == 1
    source, destination = replacements[0]
    assert destination == store.path
    assert source.parent == store.path.parent
    assert not source.exists()


def test_dispatcher_enqueues_each_schedule_at_most_once_per_local_day(tmp_path):
    store = make_store(tmp_path)
    store.create(
        schedule_id="second-task",
        task_id="src.task.TacetTask.TacetTask",
        run_at="04:30",
        timezone="Asia/Shanghai",
        missed_window_minutes=120,
    )
    queued = []
    dispatcher = CloudScheduleDispatcher(store, queued.append)

    china_0400_utc = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)
    requests = dispatcher.run_pending(china_0400_utc)
    later_requests = dispatcher.run_pending(datetime(2026, 9, 5, 21, 0, tzinfo=UTC))
    tomorrow = dispatcher.run_pending(datetime(2026, 9, 6, 20, 30, tzinfo=UTC))

    assert [item.schedule_id for item in requests] == [DEFAULT_SCHEDULE_ID]
    assert [item.schedule_id for item in later_requests] == ["second-task"]
    assert {item.schedule_id for item in tomorrow} == {
        DEFAULT_SCHEDULE_ID,
        "second-task",
    }
    assert queued == [*requests, *later_requests, *tomorrow]


def test_disabled_and_outside_window_schedules_are_not_enqueued(tmp_path):
    store = make_store(tmp_path)
    store.set_enabled(DEFAULT_SCHEDULE_ID, False)
    store.create(
        schedule_id="narrow-window",
        task_id="src.task.ForgeryTask.ForgeryTask",
        run_at="04:00",
        timezone="Asia/Shanghai",
        missed_window_minutes=10,
    )
    queued = []
    dispatcher = CloudScheduleDispatcher(store, queued.append)

    assert dispatcher.run_pending(datetime(2026, 9, 5, 19, 59, tzinfo=UTC)) == ()
    assert dispatcher.run_pending(datetime(2026, 9, 5, 20, 11, tzinfo=UTC)) == ()
    assert queued == []


def test_enqueue_failure_is_still_an_attempt_and_is_not_retried(tmp_path):
    store = make_store(tmp_path)
    calls = []

    def fail(request):
        calls.append(request)
        raise RuntimeError("queue unavailable")

    dispatcher = CloudScheduleDispatcher(store, fail)
    current = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)

    assert len(dispatcher.run_pending(current)) == 1
    assert dispatcher.run_pending(current) == ()
    assert len(calls) == 1
    assert store.attempt_for(DEFAULT_SCHEDULE_ID) == {
        "local_date": "2026-09-06",
        "attempted_at": current.isoformat(),
        "status": "enqueue_failed",
        "error_type": "RuntimeError",
    }


def test_attempt_is_recorded_before_enqueue_callback(tmp_path):
    store = make_store(tmp_path)
    observed = []

    def inspect_attempt(request):
        observed.append(store.attempt_for(request.schedule_id))

    dispatcher = CloudScheduleDispatcher(store, inspect_attempt)
    dispatcher.run_pending(datetime(2026, 9, 5, 20, 0, tzinfo=UTC))

    assert observed[0]["status"] == "enqueueing"


def test_dispatcher_rejects_naive_time(tmp_path):
    dispatcher = CloudScheduleDispatcher(make_store(tmp_path), lambda request: None)

    with pytest.raises(CloudConfigurationError, match="timezone-aware"):
        dispatcher.run_pending(datetime(2026, 9, 6, 4, 0))


def test_corrupt_document_is_reported_instead_of_overwritten(tmp_path):
    path = tmp_path / "schedules.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(CloudConfigurationError, match="cannot read cloud schedules"):
        CloudScheduleStore(path)
    assert path.read_text(encoding="utf-8") == "not json"
