"""Cross-platform Playwright frame capture for the cloud-game page.

The existing ok-script browser capture owns a browser window and uses Windows
Graphics Capture.  This adapter intentionally does neither: it receives an
already-open synchronous Playwright page and turns ``page.screenshot()`` bytes
into the BGR frames consumed by ok-script tasks.  Keeping ownership separate
lets the cloud session manage login, queueing, and tab shutdown.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


class BrowserCaptureError(RuntimeError):
    """Raised when a connected Playwright page cannot produce a valid frame."""


@dataclass(frozen=True, slots=True)
class FrameMetrics:
    """Lightweight health signals derived from the latest normalized frame."""

    width: int
    height: int
    mean_luminance: float
    black_pixel_ratio: float
    difference_ratio: float | None
    is_black: bool
    is_static: bool
    consecutive_static_frames: int


class PlaywrightBrowserCapture:
    """Capture a fixed-size 16:9 BGR frame from an existing Playwright page."""

    name = "Playwright Browser Capture"
    description = "Capture an existing Playwright page as normalized BGR frames"

    def __init__(
        self,
        page_or_call: Any,
        *,
        output_size: tuple[int, int] = (1280, 720),
        black_pixel_threshold: int = 12,
        black_ratio_threshold: float = 0.98,
        static_difference_threshold: float = 0.002,
    ) -> None:
        width, height = output_size
        if width <= 0 or height <= 0:
            raise ValueError("output dimensions must be positive")
        if width * 9 != height * 16:
            raise ValueError("output_size must use a 16:9 aspect ratio")
        if not 0 <= black_pixel_threshold <= 255:
            raise ValueError("black_pixel_threshold must be between 0 and 255")
        if not 0.0 <= black_ratio_threshold <= 1.0:
            raise ValueError("black_ratio_threshold must be between 0 and 1")
        if not 0.0 <= static_difference_threshold <= 1.0:
            raise ValueError("static_difference_threshold must be between 0 and 1")

        self.page = None if callable(page_or_call) else page_or_call
        self._page_call: Callable[[Callable[[Any], Any]], Any] | None = (
            page_or_call if callable(page_or_call) else lambda callback: callback(self.page)
        )
        self._size = (width, height)
        self.black_pixel_threshold = black_pixel_threshold
        self.black_ratio_threshold = black_ratio_threshold
        self.static_difference_threshold = static_difference_threshold
        self._previous_gray: np.ndarray | None = None
        self._consecutive_static_frames = 0
        self._last_metrics: FrameMetrics | None = None

    @property
    def width(self) -> int:
        return self._size[0]

    @property
    def height(self) -> int:
        return self._size[1]

    @property
    def last_metrics(self) -> FrameMetrics | None:
        return self._last_metrics

    @property
    def metrics(self) -> FrameMetrics | None:
        """Alias suited to callers that expose capture health in status output."""

        return self._last_metrics

    def connected(self) -> bool:
        page_call = self._page_call
        if page_call is None:
            return False
        try:
            return bool(page_call(lambda page: not bool(page.is_closed())))
        except Exception:
            return False

    def get_frame(self) -> np.ndarray | None:
        """Return the latest frame using the ok-script capture-method shape."""

        return self.do_get_frame()

    def do_get_frame(self) -> np.ndarray | None:
        page_call = self._page_call
        if page_call is None or not self.connected():
            return None

        try:
            screenshot = page_call(
                lambda page: page.screenshot(type="png", full_page=False)
            )
        except Exception as exc:
            raise BrowserCaptureError(f"cannot capture cloud-game page: {exc}") from exc

        if not isinstance(screenshot, (bytes, bytearray, memoryview)) or not screenshot:
            raise BrowserCaptureError("Playwright returned empty screenshot data")

        encoded = np.frombuffer(screenshot, dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        if decoded is None:
            raise BrowserCaptureError("Playwright screenshot is not a decodable image")

        frame = self._as_bgr(decoded)
        frame = self._normalize_16_9(frame)
        self._update_metrics(frame)
        return frame

    def close(self) -> None:
        """Detach without closing the externally owned browser tab."""

        self.page = None
        self._page_call = None
        self._previous_gray = None
        self._consecutive_static_frames = 0

    @staticmethod
    def _as_bgr(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if frame.ndim != 3:
            raise BrowserCaptureError(
                f"unsupported screenshot dimensions: {frame.shape}"
            )
        channels = frame.shape[2]
        if channels == 3:
            return frame
        if channels == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        raise BrowserCaptureError(f"unsupported screenshot channel count: {channels}")

    def _normalize_16_9(self, frame: np.ndarray) -> np.ndarray:
        source_height, source_width = frame.shape[:2]
        if source_width <= 0 or source_height <= 0:
            raise BrowserCaptureError("Playwright screenshot has invalid dimensions")

        # Crop equally from opposite edges so browser chrome or letterboxing
        # cannot distort the coordinate system expected by existing tasks.
        if source_width * 9 > source_height * 16:
            crop_width = source_height * 16 // 9
            left = (source_width - crop_width) // 2
            frame = frame[:, left : left + crop_width]
        elif source_width * 9 < source_height * 16:
            crop_height = source_width * 9 // 16
            top = (source_height - crop_height) // 2
            frame = frame[top : top + crop_height, :]

        target_width, target_height = self._size
        if frame.shape[1] != target_width or frame.shape[0] != target_height:
            shrinking = frame.shape[1] > target_width or frame.shape[0] > target_height
            interpolation = cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR
            frame = cv2.resize(
                frame,
                (target_width, target_height),
                interpolation=interpolation,
            )
        return np.ascontiguousarray(frame)

    def _update_metrics(self, frame: np.ndarray) -> None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        black_ratio = float(np.count_nonzero(gray <= self.black_pixel_threshold)) / float(
            gray.size
        )

        difference_ratio: float | None = None
        is_static = False
        if self._previous_gray is not None:
            difference = cv2.absdiff(gray, self._previous_gray)
            difference_ratio = float(np.mean(difference)) / 255.0
            is_static = difference_ratio <= self.static_difference_threshold

        if is_static:
            self._consecutive_static_frames += 1
        else:
            self._consecutive_static_frames = 0

        self._last_metrics = FrameMetrics(
            width=self.width,
            height=self.height,
            mean_luminance=float(np.mean(gray)),
            black_pixel_ratio=black_ratio,
            difference_ratio=difference_ratio,
            is_black=black_ratio >= self.black_ratio_threshold,
            is_static=is_static,
            consecutive_static_frames=self._consecutive_static_frames,
        )
        self._previous_gray = gray


# Short compatibility name for callers that do not need to know the transport.
BrowserCapture = PlaywrightBrowserCapture
