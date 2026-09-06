from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from extensions.cloud.preflight import (
    CloudPreflightError,
    CloudPreflightTask,
    ReadOnlyPreflightInteraction,
    analyze_frame,
    create_preflight_task_runner,
)


class FakeInteraction:
    def __init__(self) -> None:
        self.capture = object()
        self.calls: list[object] = []

    def should_capture(self):
        return True

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def release_all(self):
        self.calls.append("release_all")

    def release_all_keys(self):
        self.calls.append("release_all_keys")


def test_analyze_frame_reports_image_health_without_content():
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    frame[:, 80:] = 255

    report = analyze_frame(frame)

    assert (report.width, report.height) == (160, 90)
    assert report.mean_luminance == pytest.approx(127.5)
    assert report.luminance_stddev == pytest.approx(127.5)
    assert report.black_pixel_ratio == pytest.approx(0.5)
    assert 0 < report.edge_pixel_ratio < 0.02


def test_analyze_frame_rejects_non_bgr_input():
    with pytest.raises(CloudPreflightError, match="three-channel BGR"):
        analyze_frame(np.zeros((10, 10), dtype=np.uint8))


@pytest.mark.parametrize(
    "method,args",
    [
        ("click", (1, 2)),
        ("send_key", ("space",)),
        ("send_key_down", ("w",)),
        ("send_key_up", ("w",)),
        ("input_text", ("value",)),
        ("swipe", (1, 2, 3, 4, 10)),
        ("mouse_down", (1, 2)),
        ("mouse_up", ()),
        ("scroll", (1, 2, 3)),
    ],
)
def test_read_only_interaction_blocks_consuming_actions(method, args):
    original = FakeInteraction()
    guard = ReadOnlyPreflightInteraction(original, allow_pointer_move=False)

    with pytest.raises(CloudPreflightError, match="blocked input action"):
        getattr(guard, method)(*args)

    assert original.calls == []


def test_read_only_interaction_only_moves_after_explicit_opt_in():
    original = FakeInteraction()
    disabled = ReadOnlyPreflightInteraction(original, allow_pointer_move=False)
    enabled = ReadOnlyPreflightInteraction(original, allow_pointer_move=True)

    with pytest.raises(CloudPreflightError, match="movement is disabled"):
        disabled.move(10, 20)
    enabled.move(10, 20)
    enabled.release_all()

    assert original.calls == [("move", 10, 20), "release_all"]
    assert enabled.capture is original.capture


def test_preflight_factory_runs_task_class_with_bounded_timeout(monkeypatch):
    calls = []
    monkeypatch.setenv("OK_WW_CLOUD_PREFLIGHT_TIMEOUT", "12.5")
    monkeypatch.setattr(
        "extensions.cloud.preflight.run_ok_task",
        lambda adapter, task, timeout_seconds: calls.append(
            (adapter, task, timeout_seconds)
        ),
    )

    adapter = object()
    create_preflight_task_runner()(adapter)

    assert calls == [(adapter, CloudPreflightTask, 12.5)]


def test_preflight_task_captures_and_runs_ocr_without_input(monkeypatch):
    original = FakeInteraction()
    manager = SimpleNamespace(interaction=original)
    frame = np.full((90, 160, 3), 64, dtype=np.uint8)
    task = object.__new__(CloudPreflightTask)
    task._executor = SimpleNamespace(device_manager=manager)
    task.next_frame = lambda: frame
    task.ocr = lambda **kwargs: [object(), object()]
    task.info = {}
    task.info_set = lambda key, value: task.info.__setitem__(key, value)
    task.log_info = lambda message: task.info.__setitem__("Log", message)
    monkeypatch.delenv("OK_WW_CLOUD_PREFLIGHT_MOVE_POINTER", raising=False)

    task.run()

    assert isinstance(manager.interaction, ReadOnlyPreflightInteraction)
    assert original.calls == []
    assert task.info["Resolution"] == "160x90"
    assert task.info["OCR Region Count"] == 2
    assert task.info["Pointer Probe"] == "disabled"


def test_preflight_task_optional_pointer_probe_only_moves(monkeypatch):
    original = FakeInteraction()
    manager = SimpleNamespace(interaction=original)
    frame = np.full((90, 160, 3), 64, dtype=np.uint8)
    task = object.__new__(CloudPreflightTask)
    task._executor = SimpleNamespace(device_manager=manager)
    task.next_frame = lambda: frame
    task.ocr = lambda **kwargs: []
    task.move_relative = lambda x, y: manager.interaction.move(80, 45)
    task.info_set = lambda key, value: None
    task.log_info = lambda message: None
    monkeypatch.setenv("OK_WW_CLOUD_PREFLIGHT_MOVE_POINTER", "true")

    task.run()

    assert original.calls == [("move", 80, 45)]
