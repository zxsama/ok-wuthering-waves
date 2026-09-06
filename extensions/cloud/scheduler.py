"""Portable daily scheduler for isolated cloud-runner workers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as wall_time, timedelta
from pathlib import Path
from typing import IO, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import CloudConfigurationError, CloudRunnerError


logger = logging.getLogger(__name__)


class SchedulerAlreadyRunning(CloudRunnerError):
    """Raised when another scheduler owns the configured lock."""


def parse_run_at(value: str) -> wall_time:
    """Parse a strict 24-hour ``HH:MM`` value."""

    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise CloudConfigurationError("schedule time must use 24-hour HH:MM format") from exc
    if parsed.strftime("%H:%M") != value:
        raise CloudConfigurationError("schedule time must use zero-padded HH:MM format")
    return parsed


def parse_timezone(value: str) -> ZoneInfo:
    """Resolve an IANA timezone name through the standard library."""

    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CloudConfigurationError(f"unknown IANA timezone: {value}") from exc


@dataclass(frozen=True, slots=True)
class DailySchedule:
    run_at: wall_time
    timezone: ZoneInfo
    missed_window: timedelta

    @classmethod
    def create(
        cls,
        run_at: str,
        timezone: str,
        missed_window_minutes: int,
    ) -> "DailySchedule":
        if missed_window_minutes <= 0:
            raise CloudConfigurationError("missed window must be greater than zero minutes")
        return cls(
            run_at=parse_run_at(run_at),
            timezone=parse_timezone(timezone),
            missed_window=timedelta(minutes=missed_window_minutes),
        )

    def target_for(self, day: date) -> datetime:
        return datetime.combine(day, self.run_at, tzinfo=self.timezone)

    @property
    def key(self) -> str:
        return "|".join(
            (
                self.timezone.key,
                self.run_at.strftime("%H:%M"),
                str(int(self.missed_window.total_seconds())),
            )
        )


class SingleInstanceFileLock:
    """Non-blocking advisory lock implemented for Windows and POSIX."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    def __enter__(self) -> "SingleInstanceFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            self._lock(handle)
        except OSError as exc:
            handle.close()
            raise SchedulerAlreadyRunning(
                f"another cloud process owns the lock: {self.path}"
            ) from exc
        self._handle = handle
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None:
            return
        try:
            self._unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    @staticmethod
    def _lock(handle: IO[bytes]) -> None:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SubprocessWorker:
    """Start a fresh process for every scheduled run."""

    def __init__(self, command: Sequence[str]) -> None:
        self.command = tuple(command)

    def __call__(self) -> int:
        return subprocess.run(self.command, check=False).returncode


class DailyScheduler:
    """Run a worker at most once per local calendar day."""

    def __init__(
        self,
        schedule: DailySchedule,
        worker: Callable[[], int],
        *,
        state_path: Path,
        lock_path: Path,
        now: Callable[[ZoneInfo], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.schedule = schedule
        self.worker = worker
        self.state_path = state_path
        self.lock_path = lock_path
        self._now = now or (lambda timezone: datetime.now(timezone))
        self._sleep = sleep

    def run_forever(self) -> None:
        with SingleInstanceFileLock(self.lock_path):
            while True:
                current = self._now(self.schedule.timezone)
                try:
                    self.run_pending(current)
                except Exception:
                    logger.exception("scheduled cloud worker failed")
                self._sleep(
                    min(60.0, self.seconds_until_next_run(self._now(self.schedule.timezone)))
                )

    def run_pending(self, current: datetime | None = None) -> bool:
        current = self._normalize_time(current)
        target = self.schedule.target_for(current.date())
        elapsed = self._elapsed(current, target)
        if elapsed < timedelta(0) or elapsed > self.schedule.missed_window:
            return False

        state = self._load_state()
        if self._attempted_on(state, current.date()):
            return False

        state = {
            "schedule_key": self.schedule.key,
            "last_attempt_date": current.date().isoformat(),
            "last_started_at": current.isoformat(),
            "last_finished_at": None,
            "last_status": "running",
            "last_return_code": None,
            "last_error_type": None,
        }
        self._save_state(state)
        try:
            return_code = self.worker()
        except Exception as exc:
            state.update(
                last_finished_at=self._now(self.schedule.timezone).isoformat(),
                last_status="failed",
                last_error_type=type(exc).__name__,
            )
            self._save_state(state)
            raise
        else:
            state.update(
                last_finished_at=self._now(self.schedule.timezone).isoformat(),
                last_status="succeeded" if return_code == 0 else "failed",
                last_return_code=return_code,
            )
            self._save_state(state)
        return True

    def seconds_until_next_run(self, current: datetime | None = None) -> float:
        current = self._normalize_time(current)
        target = self.schedule.target_for(current.date())
        state = self._load_state()
        attempted_today = self._attempted_on(state, current.date())
        if current < target:
            next_target = target
        elif not attempted_today and self._elapsed(current, target) <= self.schedule.missed_window:
            return 1.0
        else:
            next_target = self.schedule.target_for(current.date() + timedelta(days=1))
        return max(1.0, self._elapsed(next_target, current).total_seconds())

    def _normalize_time(self, current: datetime | None) -> datetime:
        if current is None:
            return self._now(self.schedule.timezone)
        if current.tzinfo is None:
            raise CloudConfigurationError("scheduler time must be timezone-aware")
        return current.astimezone(self.schedule.timezone)

    def _attempted_on(self, state: dict[str, Any], day: date) -> bool:
        schedule_key = state.get("schedule_key")
        return (
            state.get("last_attempt_date") == day.isoformat()
            and (schedule_key is None or schedule_key == self.schedule.key)
        )

    @staticmethod
    def _elapsed(later: datetime, earlier: datetime) -> timedelta:
        return later.astimezone(UTC) - earlier.astimezone(UTC)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CloudConfigurationError(
                f"cannot read scheduler state: {self.state_path}"
            ) from exc
        if not isinstance(value, dict):
            raise CloudConfigurationError(f"invalid scheduler state: {self.state_path}")
        return value

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)
