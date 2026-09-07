"""ok-script WebRuntime schedule manager backed by portable cloud schedules."""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable

from ok.util.windows_schedule import (
    ScheduleTaskInfo,
    TriggerType,
    normalize_trigger_type,
)

from .cloud_schedules import (
    DEFAULT_MISSED_WINDOW_MINUTES,
    DEFAULT_TIMEZONE,
    CloudSchedule,
    CloudScheduleDispatcher,
    CloudScheduleStore,
    ScheduledTaskRequest,
    _parse_timezone,
)
from .models import CloudConfigurationError


@dataclass
class CloudScheduleTaskInfo(ScheduleTaskInfo):
    """ScheduleTaskInfo with the edit fields consumed by ok-script Web UI."""

    start_hour: int = 9
    start_minute: int = 0
    timeout_hours: int = 0
    auto_exit: bool = True
    email_report: bool = True


class CloudScheduleCache:
    """Small in-memory compatibility cache; JSON persistence belongs to the store."""

    def __init__(self) -> None:
        self.cache: dict[str, ScheduleTaskInfo] = {}
        self._lock = threading.RLock()

    def replace(self, values) -> None:
        with self._lock:
            self.cache = {item.path or item.name: item for item in values}

    def get(self, name: str):
        with self._lock:
            if name in self.cache:
                return self.cache[name]
            matches = [item for item in self.cache.values() if item.name == name]
            return matches[0] if len(matches) == 1 else None

    def values(self):
        with self._lock:
            return list(self.cache.values())

    def get_all(self):
        return self.values()


class CloudScheduleManager:
    """Drop-in schedule manager for the existing ok-script browser UI."""

    ROOT_PATH = "\\Cloud"

    def __init__(
        self,
        path: Path,
        enqueue: Callable[[ScheduledTaskRequest], object],
        *,
        timezone: str = DEFAULT_TIMEZONE,
        missed_window_minutes: int = DEFAULT_MISSED_WINDOW_MINUTES,
        poll_interval: float = 30.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = CloudScheduleStore(Path(path))
        self.enqueue = enqueue
        self.timezone = timezone
        self.missed_window_minutes = missed_window_minutes
        self.poll_interval = max(0.1, float(poll_interval))
        self._now = now or (lambda: datetime.now(UTC))
        self.dispatcher = CloudScheduleDispatcher(self.store, enqueue, now=self._now)
        self.cache = CloudScheduleCache()
        self.lock = threading.RLock()
        self.update_callbacks: list[Callable[[ScheduleTaskInfo], None]] = []
        self.running = False
        self.sync_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.query_all_tasks(force_sync=True)

    def register_update_callback(self, callback) -> None:
        with self.lock:
            if callback not in self.update_callbacks:
                self.update_callbacks.append(callback)

    def unregister_update_callback(self, callback) -> None:
        with self.lock:
            if callback in self.update_callbacks:
                self.update_callbacks.remove(callback)

    def query_all_tasks(self, force_sync: bool = False):
        if force_sync or not self.cache.values():
            values = [self._task_info(item) for item in self.store.list()]
            self.cache.replace(values)
        return self.cache.values()

    def create_task(
        self,
        task_name: str,
        task_index: int,
        trigger_type: TriggerType,
        timeout_hours: int = 0,
        start_hour: int = 9,
        start_minute: int = 0,
        auto_exit: bool = True,
        email_report: bool = True,
        enabled: bool = True,
        description: str = "",
        interval_days: int = 0,
        interval_hours: int = 0,
        task_identifier: str | None = None,
    ) -> bool:
        try:
            trigger = normalize_trigger_type(
                trigger_type.value if isinstance(trigger_type, TriggerType) else trigger_type
            )
            task_id = str(task_identifier or "").strip()
            if not task_id:
                return False
            hour, minute = self._validate_time(start_hour, start_minute)
            days, hours = self._normalize_intervals(trigger, interval_days, interval_hours)
            now = self._now().astimezone(_parse_timezone(self.timezone))
            start_day = now.date()
            if trigger == TriggerType.ONCE and (hour, minute) <= (now.hour, now.minute):
                start_day += timedelta(days=1)
            schedule_id = self._new_id(task_name)
            schedule = self.store.create(
                schedule_id=schedule_id,
                task_id=task_id,
                run_at=f"{hour:02d}:{minute:02d}",
                timezone=self.timezone,
                missed_window_minutes=self.missed_window_minutes,
                enabled=bool(enabled),
                name=str(task_name or "").strip() or task_id.rsplit(".", 1)[-1],
                trigger_type=trigger.value,
                interval_days=days,
                interval_hours=hours,
                timeout_hours=max(0, int(timeout_hours)),
                auto_exit=bool(auto_exit),
                email_report=bool(email_report),
                start_date=start_day.isoformat(),
                task_index=int(task_index),
            )
            self._refresh_and_notify(schedule.id)
            return True
        except (CloudConfigurationError, TypeError, ValueError):
            return False

    def replace_task(
        self,
        task_name: str,
        task_index: int,
        trigger_type: TriggerType,
        timeout_hours: int = 0,
        start_hour: int = 9,
        start_minute: int = 0,
        auto_exit: bool = True,
        email_report: bool | None = None,
        enabled: bool = True,
        description: str = "",
        interval_days: int = 0,
        interval_hours: int = 0,
        task_identifier: str | None = None,
    ) -> bool:
        try:
            current = self._resolve(task_name)
            trigger = normalize_trigger_type(
                trigger_type.value if isinstance(trigger_type, TriggerType) else trigger_type
            )
            hour, minute = self._validate_time(start_hour, start_minute)
            days, hours = self._normalize_intervals(trigger, interval_days, interval_hours)
            now = self._now().astimezone(_parse_timezone(self.timezone))
            start_day = now.date()
            if trigger == TriggerType.ONCE and (hour, minute) <= (now.hour, now.minute):
                start_day += timedelta(days=1)
            updated = self.store.update(
                self._id_from_path(current.path),
                task_id=str(task_identifier or current.task_identifier),
                run_at=f"{hour:02d}:{minute:02d}",
                timezone=self.timezone,
                missed_window_minutes=self.missed_window_minutes,
                enabled=bool(enabled),
                trigger_type=trigger.value,
                interval_days=days,
                interval_hours=hours,
                timeout_hours=max(0, int(timeout_hours)),
                auto_exit=bool(auto_exit),
                email_report=(
                    current.email_report if email_report is None else bool(email_report)
                ),
                start_date=start_day.isoformat(),
                task_index=int(task_index),
            )
            self._refresh_and_notify(updated.id)
            return True
        except (CloudConfigurationError, KeyError, TypeError, ValueError):
            return False

    def delete_task(self, task_name: str) -> bool:
        try:
            current = self._resolve(task_name)
            self.store.delete(self._id_from_path(current.path))
            self.query_all_tasks(force_sync=True)
            return True
        except (CloudConfigurationError, KeyError, ValueError):
            return False

    def enable_task(self, task_name: str) -> bool:
        return self._set_enabled(task_name, True)

    def disable_task(self, task_name: str) -> bool:
        return self._set_enabled(task_name, False)

    def run_pending(self, current: datetime | None = None):
        requests = self.dispatcher.run_pending(current)
        if requests:
            self.query_all_tasks(force_sync=True)
        return requests

    def start(self) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self._stop_event.clear()
            self.sync_thread = threading.Thread(
                target=self._run_loop, daemon=True, name="CloudScheduleManager"
            )
            self.sync_thread.start()

    def stop(self) -> None:
        with self.lock:
            self.running = False
            thread = self.sync_thread
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=2.0)
        self.sync_thread = None

    def start_background_sync(self, interval: int = 60) -> None:
        self.poll_interval = max(0.1, float(interval))
        self.start()

    stop_background_sync = stop

    def _run_loop(self) -> None:
        while self.running:
            try:
                self.run_pending()
            except Exception:
                # The dispatcher's persistent attempt record prevents tight retries.
                pass
            self._stop_event.wait(self.poll_interval)

    def _set_enabled(self, task_name: str, enabled: bool) -> bool:
        try:
            current = self._resolve(task_name)
            updated = self.store.set_enabled(self._id_from_path(current.path), enabled)
            self._refresh_and_notify(updated.id)
            return True
        except (CloudConfigurationError, KeyError, ValueError):
            return False

    def _refresh_and_notify(self, schedule_id: str) -> None:
        self.query_all_tasks(force_sync=True)
        info = self.cache.get(self._path(schedule_id))
        if info is None:
            return
        for callback in tuple(self.update_callbacks):
            callback(info)

    def _resolve(self, value: str) -> ScheduleTaskInfo:
        result = self.cache.get(str(value))
        if result is None:
            self.query_all_tasks(force_sync=True)
            result = self.cache.get(str(value))
        if result is None:
            raise KeyError(value)
        return result

    def _task_info(self, schedule: CloudSchedule) -> CloudScheduleTaskInfo:
        hour, minute = (int(part) for part in schedule.run_at.split(":"))
        return CloudScheduleTaskInfo(
            name=schedule.name or schedule.task_id.rsplit(".", 1)[-1],
            path=self._path(schedule.id),
            enabled=schedule.enabled,
            status="Ready" if schedule.enabled else "Disabled",
            trigger_type=schedule.trigger_type,
            next_run_time=self._next_run_time(schedule),
            actions=f"enqueue {schedule.task_id}",
            author="ok-script cloud",
            description="Cloud schedule",
            task_index=schedule.task_index,
            task_identifier=schedule.task_id,
            interval_days=schedule.interval_days,
            interval_hours=schedule.interval_hours,
            read_only=False,
            start_hour=hour,
            start_minute=minute,
            timeout_hours=schedule.timeout_hours,
            auto_exit=schedule.auto_exit,
            email_report=schedule.email_report,
        )

    def _next_run_time(self, schedule: CloudSchedule) -> str:
        timezone = _parse_timezone(schedule.timezone)
        now = self._now().astimezone(timezone)
        hour, minute = (int(part) for part in schedule.run_at.split(":"))
        start_day = date.fromisoformat(schedule.start_date) if schedule.start_date else now.date()
        candidate = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone).replace(
            hour=hour, minute=minute
        )
        trigger = schedule.trigger_type
        if trigger == "Once":
            return candidate.strftime("%Y-%m-%d %H:%M") if candidate >= now else "-"
        if trigger == "Daily":
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate < now:
                candidate += timedelta(days=1)
        elif trigger == "Weekly":
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            candidate -= timedelta(days=candidate.weekday())
            if candidate < now:
                candidate += timedelta(days=7)
        elif trigger == "Monthly":
            candidate = now.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
            if candidate < now:
                candidate = (candidate.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            delta = timedelta(
                hours=schedule.interval_hours,
                days=schedule.interval_days,
            )
            while candidate < now:
                candidate += delta
        return candidate.strftime("%Y-%m-%d %H:%M")

    @classmethod
    def _path(cls, schedule_id: str) -> str:
        return f"{cls.ROOT_PATH}\\{schedule_id}"

    @classmethod
    def _id_from_path(cls, value: str) -> str:
        prefix = cls.ROOT_PATH + "\\"
        if not str(value).startswith(prefix):
            raise ValueError("invalid cloud schedule path")
        return str(value)[len(prefix):]

    @staticmethod
    def _new_id(task_name: str) -> str:
        prefix = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(task_name or "schedule")).strip(".-")
        return f"{prefix[:48] or 'schedule'}-{uuid.uuid4().hex[:10]}"

    @staticmethod
    def _validate_time(hour: int, minute: int) -> tuple[int, int]:
        hour, minute = int(hour), int(minute)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("invalid schedule time")
        return hour, minute

    @staticmethod
    def _normalize_intervals(trigger, interval_days, interval_hours):
        days, hours = max(0, int(interval_days)), max(0, int(interval_hours))
        if trigger != TriggerType.CUSTOM:
            return 0, 0
        if days > 0:
            return days, 0
        return 0, hours or 1
