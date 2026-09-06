import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from extensions.cloud.models import CloudConfigurationError
from extensions.cloud.scheduler import (
    DailySchedule,
    DailyScheduler,
    SchedulerAlreadyRunning,
    SingleInstanceFileLock,
    SubprocessWorker,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def make_scheduler(tmp_path, worker, *, window=120):
    return DailyScheduler(
        DailySchedule.create("04:00", "Asia/Shanghai", window),
        worker,
        state_path=tmp_path / "scheduler-state.json",
        lock_path=tmp_path / "scheduler.lock",
    )


def test_schedule_validates_time_timezone_and_window():
    assert DailySchedule.create("04:05", "Asia/Shanghai", 30).run_at.hour == 4

    with pytest.raises(CloudConfigurationError):
        DailySchedule.create("4:05", "Asia/Shanghai", 30)
    with pytest.raises(CloudConfigurationError):
        DailySchedule.create("04:05", "not/a-zone", 30)
    with pytest.raises(CloudConfigurationError):
        DailySchedule.create("04:05", "Asia/Shanghai", 0)


def test_scheduler_runs_only_once_per_local_day(tmp_path):
    calls = []
    scheduler = make_scheduler(tmp_path, lambda: calls.append("run") or 0)

    assert scheduler.run_pending(datetime(2026, 9, 6, 4, 0, tzinfo=SHANGHAI)) is True
    assert scheduler.run_pending(datetime(2026, 9, 6, 4, 30, tzinfo=SHANGHAI)) is False
    assert scheduler.run_pending(datetime(2026, 9, 7, 4, 0, tzinfo=SHANGHAI)) is True
    assert calls == ["run", "run"]


def test_scheduler_honors_missed_window(tmp_path):
    calls = []
    scheduler = make_scheduler(tmp_path, lambda: calls.append("run") or 0, window=30)

    assert scheduler.run_pending(datetime(2026, 9, 6, 3, 59, tzinfo=SHANGHAI)) is False
    assert scheduler.run_pending(datetime(2026, 9, 6, 4, 30, tzinfo=SHANGHAI)) is True
    assert scheduler.run_pending(datetime(2026, 9, 7, 4, 31, tzinfo=SHANGHAI)) is False
    assert calls == ["run"]


def test_scheduler_records_attempt_before_worker_failure(tmp_path):
    scheduler = make_scheduler(tmp_path, lambda: (_ for _ in ()).throw(RuntimeError("failed")))
    current = datetime(2026, 9, 6, 4, 0, tzinfo=SHANGHAI)

    with pytest.raises(RuntimeError, match="failed"):
        scheduler.run_pending(current)

    assert scheduler.run_pending(current) is False
    state = json.loads((tmp_path / "scheduler-state.json").read_text(encoding="utf-8"))
    assert state["last_status"] == "failed"
    assert state["last_finished_at"] is not None
    assert state["last_error_type"] == "RuntimeError"


def test_scheduler_records_nonzero_worker_return_as_failure(tmp_path):
    scheduler = make_scheduler(tmp_path, lambda: 7)

    assert scheduler.run_pending(datetime(2026, 9, 6, 4, 0, tzinfo=SHANGHAI)) is True

    state = json.loads((tmp_path / "scheduler-state.json").read_text(encoding="utf-8"))
    assert state["last_status"] == "failed"
    assert state["last_return_code"] == 7
    assert state["last_finished_at"] is not None


def test_scheduler_ignores_attempt_from_a_different_schedule(tmp_path):
    calls = []
    original = make_scheduler(tmp_path, lambda: calls.append("original") or 0)
    changed = DailyScheduler(
        DailySchedule.create("05:00", "Asia/Shanghai", 120),
        lambda: calls.append("changed") or 0,
        state_path=tmp_path / "scheduler-state.json",
        lock_path=tmp_path / "scheduler.lock",
    )

    assert original.run_pending(datetime(2026, 9, 6, 4, 0, tzinfo=SHANGHAI)) is True
    assert changed.run_pending(datetime(2026, 9, 6, 5, 0, tzinfo=SHANGHAI)) is True
    assert calls == ["original", "changed"]


def test_scheduler_does_not_repeat_legacy_attempt_after_upgrade(tmp_path):
    state_path = tmp_path / "scheduler-state.json"
    state_path.write_text(
        json.dumps({"last_attempt_date": "2026-09-06"}),
        encoding="utf-8",
    )
    calls = []
    scheduler = make_scheduler(tmp_path, lambda: calls.append("run") or 0)

    assert scheduler.run_pending(datetime(2026, 9, 6, 4, 30, tzinfo=SHANGHAI)) is False
    assert scheduler.seconds_until_next_run(
        datetime(2026, 9, 6, 4, 30, tzinfo=SHANGHAI)
    ) > 60
    assert calls == []


def test_run_forever_survives_worker_exception(tmp_path):
    calls = []
    current = [datetime(2026, 9, 6, 4, 0, tzinfo=SHANGHAI)]

    def fail_worker():
        calls.append("worker")
        if calls.count("worker") == 1:
            raise RuntimeError("failed")
        return 0

    def stop_after_iteration(seconds):
        calls.append(("sleep", seconds))
        if calls.count("worker") == 1:
            current[0] = datetime(2026, 9, 7, 4, 0, tzinfo=SHANGHAI)
        else:
            raise KeyboardInterrupt

    scheduler = DailyScheduler(
        DailySchedule.create("04:00", "Asia/Shanghai", 120),
        fail_worker,
        state_path=tmp_path / "scheduler-state.json",
        lock_path=tmp_path / "scheduler.lock",
        now=lambda _timezone: current[0],
        sleep=stop_after_iteration,
    )

    with pytest.raises(KeyboardInterrupt):
        scheduler.run_forever()

    assert calls == ["worker", ("sleep", 60.0), "worker", ("sleep", 60.0)]


def test_single_instance_lock_rejects_second_owner(tmp_path):
    path = tmp_path / "scheduler.lock"

    with SingleInstanceFileLock(path):
        with pytest.raises(SchedulerAlreadyRunning):
            with SingleInstanceFileLock(path):
                pass


def test_subprocess_worker_starts_command_without_shell(monkeypatch):
    observed = {}

    class Result:
        returncode = 7

    def fake_run(command, *, check):
        observed["command"] = command
        observed["check"] = check
        return Result()

    monkeypatch.setattr("extensions.cloud.scheduler.subprocess.run", fake_run)

    assert SubprocessWorker(["python", "-m", "worker"])() == 7
    assert observed == {"command": ("python", "-m", "worker"), "check": False}
