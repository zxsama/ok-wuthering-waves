"""Compatibility objects that let ok-script use an existing cloud-game tab.

The upstream :class:`ok.device.DeviceManager.DeviceManager` owns and replaces
capture backends in ``do_start``.  A cloud session already owns its browser,
so this module provides the smallest manager-shaped object required by the
headless task executor instead of patching ok-script or pretending to own a
native game window.
"""

from __future__ import annotations

from typing import Any

from .browser_capture import PlaywrightBrowserCapture
from .browser_interaction import PlaywrightBrowserInteraction
from .models import CloudConfigurationError


CLOUD_DEVICE_ID = "cloud-browser"
DEFAULT_FRAME_SIZE = (1280, 720)


class CloudDeviceManager:
    """A fixed browser device compatible with the ok-script task executor."""

    def __init__(
        self,
        adapter: Any,
        *,
        exit_event: Any = None,
        frame_size: tuple[int, int] = DEFAULT_FRAME_SIZE,
    ) -> None:
        try:
            # Import only the platform-neutral bases.  The compatibility
            # aggregate modules also import every Windows backend, although
            # the cloud bridge never uses any of them.
            from ok.device.capture_methods.base import BaseCaptureMethod
            from ok.device.interaction_methods.base import BaseInteraction
        except ImportError as exc:
            raise CloudConfigurationError(
                "the cloud task bridge requires ok-script and the project dependencies"
            ) from exc

        page_call = getattr(adapter, "call_page", None)
        if not callable(page_call):
            raise CloudConfigurationError(
                "cloud adapter must expose call_page(callback) for task capture and input"
            )

        self.adapter = adapter
        self.exit_event = exit_event
        self.executor = None
        self.hwnd_window = None
        self.supported_ratio = frame_size[0] / frame_size[1]
        capture_transport = PlaywrightBrowserCapture(page_call, output_size=frame_size)
        interaction_transport = PlaywrightBrowserInteraction(
            page_call, coordinate_size=frame_size, viewport=frame_size
        )

        class OkCloudCapture(BaseCaptureMethod):
            name = capture_transport.name
            description = capture_transport.description

            def __init__(self) -> None:
                super().__init__()
                self._size = frame_size

            def do_get_frame(self):
                return capture_transport.do_get_frame()

            def connected(self) -> bool:
                return capture_transport.connected()

            def close(self) -> None:
                capture_transport.close()

            @property
            def metrics(self):
                return capture_transport.metrics

        class OkCloudInteraction(BaseInteraction):
            def __init__(self, capture: Any) -> None:
                super().__init__(capture)

            def send_key(self, key: Any, down_time: float = 0.02) -> None:
                interaction_transport.send_key(key, down_time)

            def send_key_down(self, key: Any) -> None:
                interaction_transport.send_key_down(key)

            def send_key_up(self, key: Any) -> None:
                interaction_transport.send_key_up(key)

            def should_capture(self) -> bool:
                return interaction_transport.should_capture()

            def on_run(self) -> None:
                interaction_transport.on_run()

            def input_text(self, text: str) -> None:
                interaction_transport.input_text(text)

            def swipe(self, *args: Any, **kwargs: Any) -> None:
                interaction_transport.swipe(*args, **kwargs)

            def mouse_down(self, *args: Any, **kwargs: Any) -> None:
                interaction_transport.mouse_down(*args, **kwargs)

            def mouse_up(self, *args: Any, **kwargs: Any) -> None:
                interaction_transport.mouse_up(*args, **kwargs)

            def move(self, *args: Any, **kwargs: Any) -> None:
                interaction_transport.move(*args, **kwargs)

            def click(self, *args: Any, **kwargs: Any) -> None:
                interaction_transport.click(*args, **kwargs)

            def scroll(self, *args: Any, **kwargs: Any) -> None:
                interaction_transport.scroll(*args, **kwargs)

            def release_all_keys(self) -> None:
                interaction_transport.release_all_keys()

            def release_all(self) -> None:
                interaction_transport.release_all()

            def on_destroy(self) -> None:
                interaction_transport.on_destroy()

        self.capture_method = OkCloudCapture()
        self.capture_method.exit_event = exit_event
        self.interaction = OkCloudInteraction(self.capture_method)
        # A few upstream helpers expect the interaction to expose its capture.
        self.interaction.capture = self.capture_method
        self.config: dict[str, Any] = {
            "preferred": CLOUD_DEVICE_ID,
            "capture": "browser",
            "interaction": self.interaction.__class__.__name__,
        }
        self.device_dict = {CLOUD_DEVICE_ID: self._device_payload()}

    @property
    def width(self) -> int:
        return self.capture_method.width if self.capture_method is not None else 0

    @property
    def height(self) -> int:
        return self.capture_method.height if self.capture_method is not None else 0

    @property
    def device(self) -> None:
        """Cloud play is not an ADB device."""

        return None

    def _device_payload(self) -> dict[str, Any]:
        connected = bool(self.capture_method and self.capture_method.connected())
        return {
            "address": "cloud-page",
            "imei": CLOUD_DEVICE_ID,
            "device": "browser",
            "nick": "Cloud Game",
            "width": self.width,
            "height": self.height,
            "connected": connected,
            "resolution": f"{self.width}x{self.height}",
        }

    def get_preferred_device(self) -> dict[str, Any]:
        device = self.device_dict[CLOUD_DEVICE_ID]
        device.update(self._device_payload())
        return device

    def get_devices(self) -> list[dict[str, Any]]:
        return [self.get_preferred_device()]

    def get_resolution(self, device: Any = None) -> tuple[int, int]:
        del device
        return self.width, self.height

    def get_preferred_capture(self) -> str:
        return "browser"

    def available_capture_methods(self, device: Any = None) -> list[str]:
        """Expose the single cloud capture backend to the shared Web UI."""

        del device
        return ["browser"]

    def available_interaction_methods(self, device: Any = None) -> list[str]:
        """Expose the single cloud input backend to the shared Web UI."""

        del device
        return [self.interaction.__class__.__name__]

    def set_preferred_device(self, imei: str | None = None, index: int = -1) -> bool:
        if index not in (-1, 0):
            return False
        if imei not in (None, CLOUD_DEVICE_ID):
            return False
        self.config["preferred"] = CLOUD_DEVICE_ID
        return True

    def set_capture(self, capture: str) -> bool:
        if capture != "browser":
            return False
        self.config["capture"] = capture
        return True

    def set_interaction(self, interaction: Any) -> bool:
        interaction_name = (
            interaction.__name__ if isinstance(interaction, type) else str(interaction)
        )
        if interaction_name != self.interaction.__class__.__name__:
            return False
        self.config["interaction"] = interaction_name
        return True

    def device_connected(self) -> bool:
        return bool(self.capture_method and self.capture_method.connected())

    def do_refresh(self, current: bool = False) -> bool:
        del current
        self.get_preferred_device()
        # Deliberately do not call upstream DeviceManager.do_start: it uses
        # concrete isinstance checks and would replace this external backend.
        return self.device_connected()

    def refresh(self) -> bool:
        return self.do_refresh()

    def do_start(self, notify: bool = True) -> bool:
        del notify
        return self.do_refresh()

    def start(self) -> bool:
        return self.do_start()

    def update_resolution_for_hwnd(self) -> None:
        return None

    def get_exe_path(self, device: Any) -> None:
        del device
        return None

    def stop_hwnd(self) -> None:
        """The cloud runner closes the browser tab after the task finishes."""

        return None

    def ensure_capture(self, config: dict[str, Any] | None = None) -> "CloudDeviceManager":
        del config
        return self

    def update_capture(self, config: dict[str, Any] | None = None) -> "CloudDeviceManager":
        del config
        return self

    def close(self) -> None:
        release_error: BaseException | None = None
        interaction = self.interaction
        if interaction is not None:
            try:
                interaction.release_all()
            except BaseException as exc:
                release_error = exc
        capture = self.capture_method
        self.capture_method = None
        if capture is not None:
            capture.close()
        if release_error is not None:
            raise release_error


def create_cloud_ok_class(manager: CloudDeviceManager) -> type:
    """Create an ``OK`` subclass whose device-manager initialization is injected.

    Imports are intentionally delayed so importing the cloud runner still gives
    a useful error in a lightweight environment without ok-script installed.
    """

    try:
        from ok import ExitEvent, OK, og
    except ImportError as exc:
        raise CloudConfigurationError(
            "the DailyTask runner requires ok-script and the project dependencies; "
            "install this repository's Python environment first"
        ) from exc

    class CloudOK(OK):
        def __init__(self, config: dict[str, Any]) -> None:
            # OK keeps these as class attributes by default.  Per-run values
            # prevent a completed scheduled invocation leaking its set event.
            self.exit_event = ExitEvent()
            self.device_manager = manager
            manager.exit_event = self.exit_event
            if manager.capture_method is not None:
                manager.capture_method.exit_event = self.exit_event
            super().__init__(config)

        def init_device_manager(self) -> None:
            self.device_manager = manager
            manager.exit_event = self.exit_event
            if manager.capture_method is not None:
                manager.capture_method.exit_event = self.exit_event
            og.device_manager = manager

    return CloudOK
