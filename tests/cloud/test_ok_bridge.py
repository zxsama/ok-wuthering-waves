from __future__ import annotations

import numpy as np
import pytest

from extensions.cloud.models import CloudConfigurationError
from extensions.cloud.ok_bridge import CLOUD_DEVICE_ID, CloudDeviceManager


class FakePage:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self):
        return self.closed


class FakeAdapter:
    def __init__(self) -> None:
        self.page = FakePage()

    def call_page(self, callback):
        return callback(self.page)


def test_cloud_device_manager_exposes_fixed_connected_browser(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(
        "extensions.cloud.ok_bridge.PlaywrightBrowserCapture.do_get_frame",
        lambda self: np.zeros((720, 1280, 3), dtype=np.uint8),
    )

    manager = CloudDeviceManager(adapter)

    assert manager.get_preferred_device() == {
        "address": "cloud-page",
        "imei": CLOUD_DEVICE_ID,
        "device": "browser",
        "nick": "Cloud Game",
        "width": 1280,
        "height": 720,
        "connected": True,
        "resolution": "1280x720",
    }
    assert manager.device_connected()
    assert manager.do_refresh()
    assert manager.do_start()
    assert manager.interaction.capture is manager.capture_method
    assert manager.available_capture_methods() == ["browser"]
    assert manager.available_interaction_methods() == [
        manager.interaction.__class__.__name__
    ]
    assert manager.set_preferred_device(CLOUD_DEVICE_ID)
    assert not manager.set_preferred_device("native-window")
    assert manager.set_capture("browser")
    assert not manager.set_capture("windows")
    assert manager.set_interaction(manager.interaction.__class__.__name__)
    assert not manager.set_interaction("Pynput")


def test_cloud_device_manager_never_closes_external_page():
    adapter = FakeAdapter()
    manager = CloudDeviceManager(adapter)

    manager.stop_hwnd()
    manager.close()

    assert not adapter.page.closed
    assert manager.capture_method is None


def test_cloud_device_manager_releases_input_before_capture_close(monkeypatch):
    manager = CloudDeviceManager(FakeAdapter())
    calls = []
    monkeypatch.setattr(manager.interaction, "release_all", lambda: calls.append("release"))
    monkeypatch.setattr(manager.capture_method, "close", lambda: calls.append("capture"))

    manager.close()

    assert calls == ["release", "capture"]


def test_cloud_device_manager_still_closes_capture_when_release_fails(monkeypatch):
    manager = CloudDeviceManager(FakeAdapter())
    calls = []

    def fail_release():
        raise RuntimeError("release failed")

    monkeypatch.setattr(manager.interaction, "release_all", fail_release)
    monkeypatch.setattr(manager.capture_method, "close", lambda: calls.append("capture"))

    with pytest.raises(RuntimeError, match="release failed"):
        manager.close()

    assert calls == ["capture"]
    assert manager.capture_method is None


def test_cloud_device_manager_requires_page_dispatcher():
    with pytest.raises(CloudConfigurationError, match="call_page"):
        CloudDeviceManager(object())
