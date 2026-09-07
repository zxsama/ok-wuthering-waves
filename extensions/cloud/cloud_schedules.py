"""Persistent, task-agnostic schedules for the cloud control service.

This module deliberately knows nothing about task execution.  Due schedules are
handed to an enqueue callback so the web controller remains the single owner of
the task queue and cloud browser session.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import CloudConfigurationError


DEFAULT_DAILY_TASK_ID = "src.task.DailyTask.DailyTask"
DEFAULT_SCHEDULE_ID = "default-daily-task"
DEFAULT_RUN_AT = "04:00"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_MISSED_WINDOW_MINUTES = 120

_TASK_ID_PATTERN = re.compile(
    r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$", re.ASCII
)
_SCHEDULE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_UNSET = object()


class ScheduleNotFoundError(KeyError):
    """Raised when a requested schedule does not exist."""


class DuplicateScheduleError(ValueError):
    """Raised when creating a schedule with an existing identifier."""


@dataclass(frozen=True, slots=True)
class CloudSchedule:
    """A portable schedule referring to a task by its stable module/class ID."""

    id: str
    task_id: str
    run_at: str
    timezone: str
    missed_window_minutes: int
    enabled: bool = True
    name: str = ""
    trigger_type: str = "Daily"
    interval_days: int = 0
    interval_hours: int = 0
    timeout_hours: int = 0
    auto_exit: bool = True
    email_report: bool = True
    start_date: str = ""
    task_index: int = -1

    def __post_init__(self) -> None:
        _validate_schedule_id(self.id)
        _validate_task_id(self.task_id)
        _parse_run_at(self.run_at)
        _parse_timezone(self.timezone)
        if (
            isinstance(self.missed_window_minutes, bool)
            or not isinstance(self.missed_window_minutes, int)
            or self.missed_window_minutes <= 0
        ):
            raise CloudConfigurationError(
                "missed window must be a positive integer number of minutes"
            )
        if not isinstance(self.enabled, bool):
            raise CloudConfigurationError("schedule enabled must be a boolean")
        if self.trigger_type not in {"Daily", "Weekly", "Monthly", "Once", "Custom"}:
            raise CloudConfigurationError(f"unsupported schedule trigger: {self.trigger_type}")
        for field_name, value in (
            ("interval_days", self.interval_days),
            ("interval_hours", self.interval_hours),
            ("timeout_hours", self.timeout_hours),
            ("task_index", self.task_index),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise CloudConfigurationError(f"{field_name} must be an integer")
        if self.interval_days < 0 or self.interval_hours < 0 or self.timeout_hours < 0:
            raise CloudConfigurationError("schedule intervals and timeout cannot be negative")
        if self.trigger_type == "Custom":
            if (self.interval_days > 0) == (self.interval_hours > 0):
                raise CloudConfigurationError(
                    "custom schedule requires exactly one day or hour interval"
                )
        if not isinstance(self.auto_exit, bool):
            raise CloudConfigurationError("auto_exit must be a boolean")
        if not isinstance(self.email_report, bool):
            raise CloudConfigurationError("email_report must be a boolean")
        if self.start_date:
            try:
                date.fromisoformat(self.start_date)
            except (TypeError, ValueError) as exc:
                raise CloudConfigurationError("start_date must use YYYY-MM-DD") from exc

    def target_for(self, day: date) -> datetime:
        hour, minute = _parse_run_at(self.run_at)
        return datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            tzinfo=_parse_timezone(self.timezone),
        )


@dataclass(frozen=True, slots=True)
class ScheduledTaskRequest:
    """Queue request emitted when a schedule is due."""

    schedule_id: str
    task_id: str
    due_at: datetime
    attempted_at: datetime
    claim_key: str = ""
    timeout_hours: int = 0
    auto_exit: bool = True
    email_report: bool = True
    schedule_name: str = ""


def default_daily_schedule() -> CloudSchedule:
    return CloudSchedule(
        id=DEFAULT_SCHEDULE_ID,
        task_id=DEFAULT_DAILY_TASK_ID,
        run_at=DEFAULT_RUN_AT,
        timezone=DEFAULT_TIMEZONE,
        missed_window_minutes=DEFAULT_MISSED_WINDOW_MINUTES,
        enabled=True,
    )


class CloudScheduleStore:
    """CRUD store backed by one atomically replaced JSON document."""

    _VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        with self._lock:
            if not self.path.exists():
                self._save_document(self._default_document())
            else:
                self._load_document()

    def list(self) -> tuple[CloudSchedule, ...]:
        with self._lock:
            document = self._load_document()
            return tuple(self._decode_schedule(item) for item in document["schedules"])

    def get(self, schedule_id: str) -> CloudSchedule:
        _validate_schedule_id(schedule_id)
        with self._lock:
            document = self._load_document()
            return self._find_schedule(document, schedule_id)

    def create(
        self,
        *,
        task_id: str,
        run_at: str,
        timezone: str,
        missed_window_minutes: int,
        enabled: bool = True,
        schedule_id: str | None = None,
        name: str = "",
        trigger_type: str = "Daily",
        interval_days: int = 0,
        interval_hours: int = 0,
        timeout_hours: int = 0,
        auto_exit: bool = True,
        email_report: bool = True,
        start_date: str = "",
        task_index: int = -1,
    ) -> CloudSchedule:
        schedule = CloudSchedule(
            id=schedule_id or uuid.uuid4().hex,
            task_id=task_id,
            run_at=run_at,
            timezone=timezone,
            missed_window_minutes=missed_window_minutes,
            enabled=enabled,
            name=name,
            trigger_type=trigger_type,
            interval_days=interval_days,
            interval_hours=interval_hours,
            timeout_hours=timeout_hours,
            auto_exit=auto_exit,
            email_report=email_report,
            start_date=start_date,
            task_index=task_index,
        )
        with self._lock:
            document = self._load_document()
            if any(item["id"] == schedule.id for item in document["schedules"]):
                raise DuplicateScheduleError(schedule.id)
            document["schedules"].append(self._encode_schedule(schedule))
            self._save_document(document)
        return schedule

    def update(
        self,
        schedule_id: str,
        *,
        task_id: str | object = _UNSET,
        run_at: str | object = _UNSET,
        timezone: str | object = _UNSET,
        missed_window_minutes: int | object = _UNSET,
        enabled: bool | object = _UNSET,
        name: str | object = _UNSET,
        trigger_type: str | object = _UNSET,
        interval_days: int | object = _UNSET,
        interval_hours: int | object = _UNSET,
        timeout_hours: int | object = _UNSET,
        auto_exit: bool | object = _UNSET,
        email_report: bool | object = _UNSET,
        start_date: str | object = _UNSET,
        task_index: int | object = _UNSET,
    ) -> CloudSchedule:
        _validate_schedule_id(schedule_id)
        with self._lock:
            document = self._load_document()
            current = self._find_schedule(document, schedule_id)
            changes = {
                key: value
                for key, value in {
                    "task_id": task_id,
                    "run_at": run_at,
                    "timezone": timezone,
                    "missed_window_minutes": missed_window_minutes,
                    "enabled": enabled,
                    "name": name,
                    "trigger_type": trigger_type,
                    "interval_days": interval_days,
                    "interval_hours": interval_hours,
                    "timeout_hours": timeout_hours,
                    "auto_exit": auto_exit,
                    "email_report": email_report,
                    "start_date": start_date,
                    "task_index": task_index,
                }.items()
                if value is not _UNSET
            }
            updated = replace(current, **changes)
            document["schedules"] = [
                self._encode_schedule(updated) if item["id"] == schedule_id else item
                for item in document["schedules"]
            ]
            self._save_document(document)
            return updated

    def set_enabled(self, schedule_id: str, enabled: bool) -> CloudSchedule:
        return self.update(schedule_id, enabled=enabled)

    def delete(self, schedule_id: str) -> None:
        _validate_schedule_id(schedule_id)
        with self._lock:
            document = self._load_document()
            self._find_schedule(document, schedule_id)
            document["schedules"] = [
                item for item in document["schedules"] if item["id"] != schedule_id
            ]
            document["attempts"].pop(schedule_id, None)
            self._save_document(document)

    def claim_due(self, current: datetime) -> tuple[ScheduledTaskRequest, ...]:
        """Atomically record and return schedules due at ``current``.

        Recording happens before requests reach the callback.  Consequently an
        enqueue failure cannot cause repeated attempts on the same local day.
        """

        if current.tzinfo is None:
            raise CloudConfigurationError("scheduler time must be timezone-aware")
        requests: list[ScheduledTaskRequest] = []
        with self._lock:
            document = self._load_document()
            for item in document["schedules"]:
                schedule = self._decode_schedule(item)
                if not schedule.enabled:
                    continue
                timezone = _parse_timezone(schedule.timezone)
                local_now = current.astimezone(timezone)
                local_day = local_now.date()
                target = _due_target(schedule, local_now)
                if target is None:
                    continue
                elapsed = local_now.astimezone(UTC) - target.astimezone(UTC)
                if elapsed < timedelta(0) or elapsed > timedelta(
                    minutes=schedule.missed_window_minutes
                ):
                    continue
                attempt = document["attempts"].get(schedule.id, {})
                claim_key = _claim_key(schedule, target)
                if attempt.get("claim_key", attempt.get("local_date")) == claim_key:
                    continue
                attempted_at = current.astimezone(UTC)
                attempt_value = {
                    "local_date": local_day.isoformat(),
                    "attempted_at": attempted_at.isoformat(),
                    "status": "enqueueing",
                    "error_type": None,
                }
                if claim_key != local_day.isoformat():
                    attempt_value["claim_key"] = claim_key
                document["attempts"][schedule.id] = attempt_value
                requests.append(
                    ScheduledTaskRequest(
                        schedule_id=schedule.id,
                        task_id=schedule.task_id,
                        due_at=target,
                        attempted_at=attempted_at,
                        claim_key=claim_key,
                        timeout_hours=schedule.timeout_hours,
                        auto_exit=schedule.auto_exit,
                        email_report=schedule.email_report,
                        schedule_name=schedule.name or schedule.task_id.rsplit(".", 1)[-1],
                    )
                )
            if requests:
                self._save_document(document)
        return tuple(requests)

    def record_enqueue_result(
        self, schedule_id: str, local_day: date | str, *, error: BaseException | None = None
    ) -> None:
        with self._lock:
            document = self._load_document()
            attempt = document["attempts"].get(schedule_id)
            claim_key = local_day.isoformat() if isinstance(local_day, date) else str(local_day)
            if not isinstance(attempt, dict) or attempt.get(
                "claim_key", attempt.get("local_date")
            ) != claim_key:
                return
            attempt["status"] = "enqueue_failed" if error is not None else "queued"
            attempt["error_type"] = type(error).__name__ if error is not None else None
            self._save_document(document)

    def attempt_for(self, schedule_id: str) -> dict[str, Any] | None:
        with self._lock:
            attempt = self._load_document()["attempts"].get(schedule_id)
            return dict(attempt) if isinstance(attempt, dict) else None

    @classmethod
    def _default_document(cls) -> dict[str, Any]:
        return {
            "version": cls._VERSION,
            "schedules": [cls._encode_schedule(default_daily_schedule())],
            "attempts": {},
        }

    def _load_document(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CloudConfigurationError(
                f"cannot read cloud schedules: {self.path}"
            ) from exc
        if not isinstance(value, dict) or value.get("version") != self._VERSION:
            raise CloudConfigurationError(f"invalid cloud schedules: {self.path}")
        schedules = value.get("schedules")
        attempts = value.get("attempts")
        if not isinstance(schedules, list) or not isinstance(attempts, dict):
            raise CloudConfigurationError(f"invalid cloud schedules: {self.path}")
        seen: set[str] = set()
        for item in schedules:
            schedule = self._decode_schedule(item)
            if schedule.id in seen:
                raise CloudConfigurationError(f"duplicate schedule id: {schedule.id}")
            seen.add(schedule.id)
        return value

    @staticmethod
    def _decode_schedule(value: Any) -> CloudSchedule:
        if not isinstance(value, dict):
            raise CloudConfigurationError("invalid schedule entry")
        try:
            return CloudSchedule(**value)
        except TypeError as exc:
            raise CloudConfigurationError("invalid schedule entry") from exc

    @staticmethod
    def _encode_schedule(schedule: CloudSchedule) -> dict[str, Any]:
        value = asdict(schedule)
        defaults = {
            "name": "",
            "trigger_type": "Daily",
            "interval_days": 0,
            "interval_hours": 0,
            "timeout_hours": 0,
            "auto_exit": True,
            "email_report": True,
            "start_date": "",
            "task_index": -1,
        }
        for key, default in defaults.items():
            if value.get(key) == default:
                value.pop(key, None)
        return value

    @staticmethod
    def _find_schedule(document: dict[str, Any], schedule_id: str) -> CloudSchedule:
        for item in document["schedules"]:
            if item["id"] == schedule_id:
                return CloudScheduleStore._decode_schedule(item)
        raise ScheduleNotFoundError(schedule_id)

    def _save_document(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class CloudScheduleDispatcher:
    """Find due schedules and submit them to an external task queue."""

    def __init__(
        self,
        store: CloudScheduleStore,
        enqueue: Callable[[ScheduledTaskRequest], object],
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.enqueue = enqueue
        self._now = now or (lambda: datetime.now(UTC))

    def run_pending(self, current: datetime | None = None) -> tuple[ScheduledTaskRequest, ...]:
        attempted_at = current or self._now()
        requests = self.store.claim_due(attempted_at)
        for request in requests:
            try:
                self.enqueue(request)
            except Exception as exc:
                self.store.record_enqueue_result(
                    request.schedule_id, request.claim_key, error=exc
                )
            else:
                self.store.record_enqueue_result(request.schedule_id, request.claim_key)
        return requests


def _schedule_anchor(schedule: CloudSchedule, timezone: ZoneInfo) -> datetime:
    anchor_day = date.fromisoformat(schedule.start_date) if schedule.start_date else date(1970, 1, 1)
    hour, minute = _parse_run_at(schedule.run_at)
    return datetime(
        anchor_day.year, anchor_day.month, anchor_day.day, hour, minute, tzinfo=timezone
    )


def _due_target(schedule: CloudSchedule, local_now: datetime) -> datetime | None:
    """Return the most recent eligible occurrence for the configured trigger."""

    timezone = _parse_timezone(schedule.timezone)
    anchor = _schedule_anchor(schedule, timezone)
    trigger = schedule.trigger_type
    if trigger == "Once":
        return anchor if local_now >= anchor else None
    if trigger == "Daily":
        return schedule.target_for(local_now.date())
    if trigger == "Weekly":
        day = local_now.date() - timedelta(days=local_now.weekday())
        target = schedule.target_for(day)
        return target if target >= anchor else None
    if trigger == "Monthly":
        day = local_now.date().replace(day=1)
        target = schedule.target_for(day)
        return target if target >= anchor else None
    if schedule.interval_hours > 0:
        if local_now < anchor:
            return None
        elapsed = local_now.astimezone(UTC) - anchor.astimezone(UTC)
        occurrences = int(elapsed.total_seconds() // (schedule.interval_hours * 3600))
        return (anchor.astimezone(UTC) + timedelta(hours=occurrences * schedule.interval_hours)).astimezone(timezone)
    interval_days = schedule.interval_days
    if local_now < anchor:
        return None
    elapsed_days = (local_now.date() - anchor.date()).days
    target_day = anchor.date() + timedelta(days=(elapsed_days // interval_days) * interval_days)
    return schedule.target_for(target_day)


def _claim_key(schedule: CloudSchedule, target: datetime) -> str:
    if schedule.trigger_type in {"Daily", "Weekly", "Monthly"} or (
        schedule.trigger_type == "Custom" and schedule.interval_days > 0
    ):
        return target.date().isoformat()
    return target.astimezone(UTC).isoformat()


def _parse_run_at(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise CloudConfigurationError("schedule time must use zero-padded 24-hour HH:MM")
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _parse_timezone(value: str) -> ZoneInfo:
    if not isinstance(value, str):
        raise CloudConfigurationError("schedule timezone must be an IANA name")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CloudConfigurationError(f"unknown IANA timezone: {value}") from exc


def _validate_task_id(value: str) -> None:
    if not isinstance(value, str) or not _TASK_ID_PATTERN.fullmatch(value):
        raise CloudConfigurationError("task id must use a stable module.Class name")


def _validate_schedule_id(value: str) -> None:
    if not isinstance(value, str) or not _SCHEDULE_ID_PATTERN.fullmatch(value):
        raise CloudConfigurationError("invalid schedule id")
