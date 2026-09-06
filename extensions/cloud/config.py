"""Configuration isolated from the upstream application config."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from .models import CloudConfigurationError, QueueKind


DEFAULT_CLOUD_URL = "https://mc.kurogames.com/cloud/"


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CloudConfigurationError(f"invalid boolean value: {value!r}")


def _env_float(value: str | None, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        result = float(value)
    except ValueError as exc:
        raise CloudConfigurationError(f"{name} must be a number") from exc
    if result <= 0:
        raise CloudConfigurationError(f"{name} must be greater than zero")
    return result


@dataclass(frozen=True, slots=True)
class CloudSettings:
    cloud_url: str = DEFAULT_CLOUD_URL
    data_dir: Path = Path("data/cloud")
    queue: QueueKind = QueueKind.NORMAL
    browser_visible: bool = True
    poll_interval_seconds: float = 1.0
    login_timeout_seconds: float = 900.0
    session_restore_timeout_seconds: float = 30.0
    interaction_timeout_seconds: float = 120.0
    queue_timeout_seconds: float = 7200.0
    launch_timeout_seconds: float = 600.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CloudSettings":
        source = os.environ if env is None else env
        queue_text = source.get("OK_WW_CLOUD_QUEUE", QueueKind.NORMAL.value).strip().lower()
        try:
            queue = QueueKind(queue_text)
        except ValueError as exc:
            raise CloudConfigurationError(
                f"OK_WW_CLOUD_QUEUE must be one of: {', '.join(QueueKind)}"
            ) from exc

        settings = cls(
            cloud_url=source.get("OK_WW_CLOUD_URL", DEFAULT_CLOUD_URL).strip(),
            data_dir=Path(source.get("OK_WW_CLOUD_DATA_DIR", "data/cloud")).expanduser(),
            queue=queue,
            browser_visible=_env_bool(source.get("OK_WW_CLOUD_BROWSER_VISIBLE"), True),
            poll_interval_seconds=_env_float(
                source.get("OK_WW_CLOUD_POLL_INTERVAL"), 1.0, "OK_WW_CLOUD_POLL_INTERVAL"
            ),
            login_timeout_seconds=_env_float(
                source.get("OK_WW_CLOUD_LOGIN_TIMEOUT"), 900.0, "OK_WW_CLOUD_LOGIN_TIMEOUT"
            ),
            session_restore_timeout_seconds=_env_float(
                source.get("OK_WW_CLOUD_SESSION_RESTORE_TIMEOUT"),
                30.0,
                "OK_WW_CLOUD_SESSION_RESTORE_TIMEOUT",
            ),
            interaction_timeout_seconds=_env_float(
                source.get("OK_WW_CLOUD_INTERACTION_TIMEOUT"),
                120.0,
                "OK_WW_CLOUD_INTERACTION_TIMEOUT",
            ),
            queue_timeout_seconds=_env_float(
                source.get("OK_WW_CLOUD_QUEUE_TIMEOUT"), 7200.0, "OK_WW_CLOUD_QUEUE_TIMEOUT"
            ),
            launch_timeout_seconds=_env_float(
                source.get("OK_WW_CLOUD_LAUNCH_TIMEOUT"), 600.0, "OK_WW_CLOUD_LAUNCH_TIMEOUT"
            ),
        )
        if not settings.cloud_url:
            raise CloudConfigurationError("OK_WW_CLOUD_URL cannot be empty")
        return settings

    def with_overrides(
        self,
        *,
        data_dir: Path | None = None,
        browser_visible: bool | None = None,
    ) -> "CloudSettings":
        return replace(
            self,
            data_dir=self.data_dir if data_dir is None else data_dir,
            browser_visible=self.browser_visible if browser_visible is None else browser_visible,
        )
