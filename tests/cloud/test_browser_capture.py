from __future__ import annotations

import cv2
import numpy as np
import pytest

from extensions.cloud.browser_capture import (
    BrowserCaptureError,
    PlaywrightBrowserCapture,
)


class FakePage:
    def __init__(
        self,
        frames: list[bytes | Exception],
        *,
        closed: bool = False,
    ):
        self.frames = list(frames)
        self.closed = closed
        self.screenshot_calls: list[dict[str, object]] = []

    def is_closed(self):
        return self.closed

    def screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)
        value = self.frames[0] if len(self.frames) == 1 else self.frames.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def png(frame: np.ndarray) -> bytes:
    encoded, data = cv2.imencode(".png", frame)
    assert encoded
    return data.tobytes()


def test_capture_returns_fixed_16_9_bgr_frame_from_page_screenshot():
    source = np.zeros((100, 200, 4), dtype=np.uint8)
    source[:, 20:180] = (11, 22, 233, 255)
    page = FakePage([png(source)])
    capture = PlaywrightBrowserCapture(page, output_size=(160, 90))

    frame = capture.get_frame()

    assert frame is not None
    assert frame.shape == (90, 160, 3)
    assert frame.flags.c_contiguous
    assert tuple(frame[45, 80]) == (11, 22, 233)
    assert page.screenshot_calls == [{"type": "png", "full_page": False}]
    assert capture.width == 160
    assert capture.height == 90


def test_tall_screenshot_is_center_cropped_before_resizing():
    source = np.zeros((200, 160, 3), dtype=np.uint8)
    source[55:145, :] = (10, 180, 30)
    capture = PlaywrightBrowserCapture(
        FakePage([png(source)]), output_size=(160, 90)
    )

    frame = capture.do_get_frame()

    assert frame is not None
    assert frame.shape == (90, 160, 3)
    assert tuple(frame[45, 80]) == (10, 180, 30)


def test_black_and_static_metrics_are_updated_for_each_frame():
    black = np.zeros((90, 160, 3), dtype=np.uint8)
    changed = np.full((90, 160, 3), 255, dtype=np.uint8)
    capture = PlaywrightBrowserCapture(
        FakePage([png(black), png(black), png(changed)]),
        output_size=(160, 90),
    )

    capture.get_frame()
    first = capture.last_metrics
    capture.get_frame()
    second = capture.metrics
    capture.get_frame()
    third = capture.last_metrics

    assert first is not None
    assert first.is_black
    assert first.black_pixel_ratio == 1.0
    assert first.difference_ratio is None
    assert not first.is_static
    assert first.consecutive_static_frames == 0

    assert second is not None
    assert second.is_static
    assert second.difference_ratio == 0.0
    assert second.consecutive_static_frames == 1

    assert third is not None
    assert not third.is_black
    assert not third.is_static
    assert third.difference_ratio == 1.0
    assert third.consecutive_static_frames == 0


def test_closed_page_returns_none_and_close_does_not_close_owned_page():
    page = FakePage([png(np.zeros((90, 160, 3), dtype=np.uint8))], closed=True)
    capture = PlaywrightBrowserCapture(page, output_size=(160, 90))

    assert not capture.connected()
    assert capture.get_frame() is None

    page.closed = False
    assert capture.connected()
    capture.close()
    assert not capture.connected()
    assert not page.closed


@pytest.mark.parametrize(
    "output_size",
    [(0, 720), (1280, 0), (100, 100)],
)
def test_output_size_must_be_positive_16_9(output_size):
    with pytest.raises(ValueError):
        PlaywrightBrowserCapture(FakePage([]), output_size=output_size)


def test_invalid_screenshot_data_raises_capture_error():
    capture = PlaywrightBrowserCapture(
        FakePage([b"not an image"]), output_size=(160, 90)
    )

    with pytest.raises(BrowserCaptureError, match="not a decodable image"):
        capture.get_frame()


def test_playwright_screenshot_failure_is_wrapped():
    capture = PlaywrightBrowserCapture(
        FakePage([RuntimeError("surface unavailable")]), output_size=(160, 90)
    )

    with pytest.raises(BrowserCaptureError, match="surface unavailable"):
        capture.get_frame()


def test_capture_can_dispatch_page_access_through_owner_callback():
    page = FakePage([png(np.full((90, 160, 3), 42, dtype=np.uint8))])
    calls = []
    capture = PlaywrightBrowserCapture(
        lambda callback: calls.append("call") or callback(page),
        output_size=(160, 90),
    )

    frame = capture.get_frame()

    assert frame is not None
    assert tuple(frame[0, 0]) == (42, 42, 42)
    assert calls == ["call", "call"]
