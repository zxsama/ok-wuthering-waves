"""Read-only preflight task for the cloud-game browser bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ok import BaseTask

from .models import CloudConfigurationError, CloudRunnerError
from .task_runner import run_ok_task


PREFLIGHT_MOVE_POINTER_ENV = "OK_WW_CLOUD_PREFLIGHT_MOVE_POINTER"
PREFLIGHT_TIMEOUT_ENV = "OK_WW_CLOUD_PREFLIGHT_TIMEOUT"
DEFAULT_PREFLIGHT_TIMEOUT_SECONDS = 180.0


class CloudPreflightError(CloudRunnerError):
    """Raised when the browser bridge cannot pass a read-only preflight."""


@dataclass(frozen=True, slots=True)
class PreflightFrameReport:
    width: int
    height: int
    mean_luminance: float
    luminance_stddev: float
    black_pixel_ratio: float
    edge_pixel_ratio: float


def analyze_frame(frame: np.ndarray) -> PreflightFrameReport:
    """Calculate non-sensitive health metrics for one BGR game frame."""

    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise CloudPreflightError("preflight expected a three-channel BGR frame")
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        raise CloudPreflightError("preflight received an empty frame")

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return PreflightFrameReport(
        width=width,
        height=height,
        mean_luminance=float(np.mean(gray)),
        luminance_stddev=float(np.std(gray)),
        black_pixel_ratio=float(np.count_nonzero(gray <= 12)) / float(gray.size),
        edge_pixel_ratio=float(np.count_nonzero(edges)) / float(edges.size),
    )


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CloudConfigurationError(f"{name} must be a boolean")


def _preflight_timeout() -> float:
    value = os.environ.get(PREFLIGHT_TIMEOUT_ENV)
    if value is None or not value.strip():
        return DEFAULT_PREFLIGHT_TIMEOUT_SECONDS
    try:
        timeout = float(value)
    except ValueError as exc:
        raise CloudConfigurationError(f"{PREFLIGHT_TIMEOUT_ENV} must be a number") from exc
    if timeout <= 0:
        raise CloudConfigurationError(f"{PREFLIGHT_TIMEOUT_ENV} must be positive")
    return timeout


class ReadOnlyPreflightInteraction:
    """Allow capture coordination and optional pointer motion, but no input action."""

    def __init__(self, interaction: Any, *, allow_pointer_move: bool) -> None:
        self._interaction = interaction
        self.allow_pointer_move = allow_pointer_move
        self.capture = getattr(interaction, "capture", None)

    def should_capture(self) -> bool:
        return bool(self._interaction.should_capture())

    def on_run(self) -> None:
        # The original interaction was initialized before this guard is installed.
        return None

    def move(self, x: float, y: float) -> None:
        if not self.allow_pointer_move:
            raise CloudPreflightError(
                f"pointer movement is disabled; set {PREFLIGHT_MOVE_POINTER_ENV}=true to enable it"
            )
        self._interaction.move(x, y)

    def release_all(self) -> None:
        self._interaction.release_all()

    def release_all_keys(self) -> None:
        self._interaction.release_all_keys()

    def on_destroy(self) -> None:
        self.release_all()

    @staticmethod
    def _reject(action: str) -> None:
        raise CloudPreflightError(f"preflight blocked input action: {action}")

    def send_key(self, *args: Any, **kwargs: Any) -> None:
        self._reject("send_key")

    def send_key_down(self, *args: Any, **kwargs: Any) -> None:
        self._reject("send_key_down")

    def send_key_up(self, *args: Any, **kwargs: Any) -> None:
        self._reject("send_key_up")

    def input_text(self, *args: Any, **kwargs: Any) -> None:
        self._reject("input_text")

    def swipe(self, *args: Any, **kwargs: Any) -> None:
        self._reject("swipe")

    def mouse_down(self, *args: Any, **kwargs: Any) -> None:
        self._reject("mouse_down")

    def mouse_up(self, *args: Any, **kwargs: Any) -> None:
        self._reject("mouse_up")

    def click(self, *args: Any, **kwargs: Any) -> None:
        self._reject("click")

    def scroll(self, *args: Any, **kwargs: Any) -> None:
        self._reject("scroll")


class CloudPreflightTask(BaseTask):
    """Exercise capture, image processing and OCR without gameplay actions."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.name = "Cloud Bridge Preflight"
        self.description = "Read-only cloud capture and OCR health check"
        self.support_schedule_task = False

    def run(self) -> None:
        allow_pointer_move = _env_bool(PREFLIGHT_MOVE_POINTER_ENV)
        manager = self.executor.device_manager
        original_interaction = manager.interaction
        guarded_interaction = ReadOnlyPreflightInteraction(
            original_interaction,
            allow_pointer_move=allow_pointer_move,
        )
        # Keep the guard installed through executor cleanup so no enabled
        # background task can issue an action after this one-time task returns.
        manager.interaction = guarded_interaction

        frame = self.next_frame()
        if frame is None:
            raise CloudPreflightError("preflight could not capture a game frame")
        report = analyze_frame(frame)
        if report.black_pixel_ratio >= 0.98:
            raise CloudPreflightError("preflight captured an almost entirely black frame")

        ocr_boxes = self.ocr(frame=frame, threshold=0.1)
        if allow_pointer_move:
            self.move_relative(0.5, 0.5)

        self.info_set("Resolution", f"{report.width}x{report.height}")
        self.info_set("Mean Luminance", round(report.mean_luminance, 2))
        self.info_set("Luminance StdDev", round(report.luminance_stddev, 2))
        self.info_set("Black Pixel Ratio", round(report.black_pixel_ratio, 6))
        self.info_set("Edge Pixel Ratio", round(report.edge_pixel_ratio, 6))
        self.info_set("OCR Region Count", len(ocr_boxes))
        self.info_set("Pointer Probe", "enabled" if allow_pointer_move else "disabled")
        self.log_info(
            "cloud preflight passed: "
            f"{report.width}x{report.height}, OCR regions={len(ocr_boxes)}, "
            f"pointer probe={'enabled' if allow_pointer_move else 'disabled'}"
        )


def create_preflight_task_runner():
    """CLI factory for a bounded, non-clicking ok-script preflight run."""

    def run(adapter: Any) -> None:
        run_ok_task(
            adapter,
            task=CloudPreflightTask,
            timeout_seconds=_preflight_timeout(),
        )

    return run
