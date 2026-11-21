"""Thread-safe Playwright input adapter for existing ok-script tasks.

The adapter deliberately does not retain a Playwright ``Page``.  Every action
is submitted through ``page_call`` so the cloud session can execute it on the
thread which owns the synchronous Playwright objects.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from .browser_geometry import BrowserRect
from .models import CloudPageError


_T = TypeVar("_T")
PageCall = Callable[[Callable[[Any], _T]], _T]
InputGeometry = tuple[BrowserRect, tuple[int, int]]


class PlaywrightBrowserInteraction:
    """Translate ok-script keyboard and mouse actions to Playwright calls."""

    _KEY_MAP = {
        "esc": "Escape",
        "return": "Enter",
        "enter": "Enter",
        "space": "Space",
        "backspace": "Backspace",
        "tab": "Tab",
        "left": "ArrowLeft",
        "right": "ArrowRight",
        "up": "ArrowUp",
        "down": "ArrowDown",
        "win": "Meta",
        "windows": "Meta",
        "command": "Meta",
        "cmd": "Meta",
        "cmd_l": "MetaLeft",
        "cmd_r": "MetaRight",
        "meta": "Meta",
        "alt": "Alt",
        "lalt": "Alt",
        "ralt": "Alt",
        "alt_l": "AltLeft",
        "alt_r": "AltRight",
        "alt_gr": "AltRight",
        "ctrl": "Control",
        "control": "Control",
        "lctrl": "Control",
        "rctrl": "Control",
        "lcontrol": "Control",
        "rcontrol": "Control",
        "ctrl_l": "ControlLeft",
        "ctrl_r": "ControlRight",
        "shift": "Shift",
        "lshift": "ShiftLeft",
        "rshift": "Shift",
        "shift_l": "ShiftLeft",
        "shift_r": "ShiftRight",
        "pageup": "PageUp",
        "pagedown": "PageDown",
        "page_up": "PageUp",
        "page_down": "PageDown",
        "capslock": "CapsLock",
        "caps_lock": "CapsLock",
        "numlock": "NumLock",
        "num_lock": "NumLock",
        "scrolllock": "ScrollLock",
        "scroll_lock": "ScrollLock",
        "printscreen": "PrintScreen",
        "print_screen": "PrintScreen",
        "delete": "Delete",
        "insert": "Insert",
        "home": "Home",
        "end": "End",
    }
    _BUTTONS = {"left", "middle", "right"}
    # Several upstream feature templates intentionally match only a button's
    # decorative end cap. Native window input tolerates clicks there, while the
    # streamed client may not. Move those known action anchors into the button
    # body; do not shift unrelated controls or every light-coloured button.
    _INWARD_CLICK_DIRECTIONS = {
        "boss_proceed": -1,
        "confirm_btn_hcenter_vcenter": -1,
        "confirm_btn_highlight_hcenter_vcenter": -1,
        "echo_enhance_confirm": -1,
        "gray_confirm_exit_button": -1,
        "revive_confirm_hcenter_vcenter": -1,
        "team_start_challenge": -1,
        "cancel_button_hcenter_vcenter": 1,
        "cancel_button_highlight_hcenter_vcenter": 1,
        "claim_cancel_button_hcenter_vcenter": 1,
        "gray_button_challenge": 1,
        "skip_dialog_confirm": 1,
        "skip_quest_confirm": 1,
        "skip_quest_confirm_new": 1,
    }
    _INWARD_CLICK_DISTANCE = 0.075

    def __init__(
        self,
        page_call: PageCall,
        *,
        coordinate_size: tuple[int, int] = (1280, 720),
        viewport: tuple[int, int] = (1280, 720),
        input_geometry: Callable[[Any], InputGeometry] | None = None,
    ) -> None:
        if not callable(page_call):
            raise TypeError("page_call must be callable")
        self._validate_size("coordinate_size", coordinate_size)
        self._validate_size("viewport", viewport)

        self._page_call = page_call
        self.coordinate_size = coordinate_size
        self.viewport = viewport
        self._input_geometry = input_geometry
        self._held_keys: list[str] = []
        self._held_buttons: list[str] = []
        self._cursor_position: tuple[int, int] | None = None
        self._lock = threading.RLock()

    def send_key(self, key: Any, down_time: float = 0.02) -> None:
        """Press and release one key using Playwright's native delay option."""

        mapped = self._map_key(key)
        delay = self._delay_ms(down_time)
        with self._lock:
            self._page_call(lambda page: page.keyboard.press(mapped, delay=delay))

    def send_key_down(self, key: Any) -> None:
        mapped = self._map_key(key)
        with self._lock:
            self._page_call(lambda page: page.keyboard.down(mapped))
            if mapped not in self._held_keys:
                self._held_keys.append(mapped)

    def send_key_up(self, key: Any) -> None:
        mapped = self._map_key(key)
        with self._lock:
            self._page_call(lambda page: page.keyboard.up(mapped))
            if mapped in self._held_keys:
                self._held_keys.remove(mapped)

    def should_capture(self) -> bool:
        return True

    def on_run(self) -> None:
        self.release_all()

    def input_text(self, text: str) -> None:
        value = str(text)
        with self._lock:
            self._page_call(lambda page: page.keyboard.type(value))

    def swipe(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        duration: float,
        settle_time: float = 0,
    ) -> None:
        if duration < 0 or settle_time < 0:
            raise ValueError("swipe duration and settle_time must be non-negative")
        if not all(map(math.isfinite, (from_x, from_y, to_x, to_y))):
            raise ValueError("coordinates must be finite")
        steps = max(int(duration / 20), 5)

        def action(page: Any) -> tuple[int, int]:
            bounds, size = self._geometry(page)
            start = bounds.map_point(from_x, from_y, size)
            end = bounds.map_point(to_x, to_y, size)
            page.mouse.move(*start)
            page.mouse.down(button="left")
            try:
                for step in range(1, steps + 1):
                    ratio = step / steps
                    page.mouse.move(
                        round(start[0] + (end[0] - start[0]) * ratio),
                        round(start[1] + (end[1] - start[1]) * ratio),
                    )
                    if duration:
                        page.wait_for_timeout(duration / steps)
                if settle_time:
                    page.wait_for_timeout(settle_time * 1000)
            finally:
                page.mouse.up(button="left")
            return end

        with self._lock:
            self._cursor_position = self._page_call(action)

    def mouse_down(
        self,
        x: float = -1,
        y: float = -1,
        name: str | None = None,
        key: str = "left",
    ) -> None:
        button = self._map_button(key)
        point = self._optional_point(x, y)

        def action(page: Any) -> tuple[int, int] | None:
            mapped = None
            if point is not None:
                mapped = self._map_point(page, point)
                page.mouse.move(*mapped)
            page.mouse.down(button=button)
            return mapped

        with self._lock:
            mapped = self._page_call(action)
            if mapped is not None:
                self._cursor_position = mapped
            if button not in self._held_buttons:
                self._held_buttons.append(button)

    def mouse_up(self, name: str | None = None, key: str = "left") -> None:
        del name
        button = self._map_button(key)
        with self._lock:
            self._page_call(lambda page: page.mouse.up(button=button))
            if button in self._held_buttons:
                self._held_buttons.remove(button)

    def move(self, x: float, y: float) -> None:
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("coordinates must be finite")

        def action(page: Any) -> tuple[int, int]:
            mapped = self._map_point(page, (x, y))
            page.mouse.move(*mapped)
            return mapped

        with self._lock:
            self._cursor_position = self._page_call(action)

    def click(
        self,
        x: float = -1,
        y: float = -1,
        move_back: bool = False,
        name: str | None = None,
        down_time: float = 0.01,
        move: bool = True,
        key: str = "left",
    ) -> None:
        button = self._map_button(key)
        point = self._optional_point(x, y)
        direction = self._INWARD_CLICK_DIRECTIONS.get(name)
        delay = self._delay_ms(down_time)

        with self._lock:
            previous = self._cursor_position

            def action(page: Any) -> tuple[int, int] | None:
                if point is None:
                    page.mouse.down(button=button)
                    if delay:
                        page.wait_for_timeout(delay)
                    page.mouse.up(button=button)
                    return previous
                bounds, size = self._geometry(page)
                # The button cap offset scales with the template artwork.
                offset = min(size[0], size[1] * 16 / 9) * self._INWARD_CLICK_DISTANCE
                mapped = bounds.map_point(x + offset * (direction or 0), y, size)
                page.mouse.click(*mapped, button=button, delay=delay)
                if move_back and previous is not None and move:
                    page.mouse.move(*previous)
                    return previous
                return mapped

            self._cursor_position = self._page_call(action)

    def scroll(self, x: float, y: float, scroll_amount: float) -> None:
        point = self._optional_point(x, y)
        delta_y = -float(scroll_amount) * 100.0

        def action(page: Any) -> tuple[int, int] | None:
            mapped = None
            if point is not None:
                mapped = self._map_point(page, point)
                page.mouse.move(*mapped)
            page.mouse.wheel(0, delta_y)
            return mapped

        with self._lock:
            mapped = self._page_call(action)
            if mapped is not None:
                self._cursor_position = mapped

    def release_all_keys(self) -> None:
        """Best-effort release of every key held through this adapter."""

        with self._lock:
            held = tuple(reversed(self._held_keys))
            released: list[str] = []
            errors: list[Exception] = []

            def action(page: Any) -> None:
                for key in held:
                    try:
                        page.keyboard.up(key)
                        released.append(key)
                    except Exception as exc:  # Continue to avoid stuck keys.
                        errors.append(exc)

            self._page_call(action)
            for key in released:
                if key in self._held_keys:
                    self._held_keys.remove(key)
            if errors:
                raise CloudPageError(
                    f"failed to release {len(errors)} browser key(s): {errors[0]}"
                ) from errors[0]

    def release_all(self) -> None:
        """Release held mouse buttons and keys before ending a task run."""

        with self._lock:
            buttons = tuple(reversed(self._held_buttons))
            released_buttons: list[str] = []
            errors: list[Exception] = []

            def action(page: Any) -> None:
                for button in buttons:
                    try:
                        page.mouse.up(button=button)
                        released_buttons.append(button)
                    except Exception as exc:
                        errors.append(exc)
                for key in tuple(reversed(self._held_keys)):
                    try:
                        page.keyboard.up(key)
                        self._held_keys.remove(key)
                    except Exception as exc:
                        errors.append(exc)

            self._page_call(action)
            for button in released_buttons:
                if button in self._held_buttons:
                    self._held_buttons.remove(button)
            if errors:
                raise CloudPageError(
                    f"failed to release {len(errors)} browser input(s): {errors[0]}"
                ) from errors[0]

    def on_destroy(self) -> None:
        self.release_all()

    def _optional_point(self, x: float, y: float) -> tuple[float, float] | None:
        if x == -1 and y == -1:
            return None
        if x == -1 or y == -1:
            raise ValueError("x and y must both be provided or both be -1")
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("coordinates must be finite")
        return x, y

    def _geometry(self, page: Any) -> InputGeometry:
        if self._input_geometry is not None:
            return self._input_geometry(page)
        return BrowserRect(0, 0, *self.viewport), self.coordinate_size

    def _map_point(self, page: Any, point: tuple[float, float]) -> tuple[int, int]:
        bounds, size = self._geometry(page)
        return bounds.map_point(*point, size)

    @classmethod
    def _map_key(cls, key: Any) -> str:
        value = str(key).strip()
        if not value:
            raise ValueError("key cannot be empty")
        lowered = value.lower()
        if len(value) == 1:
            return lowered
        if lowered.startswith("f") and lowered[1:].isdigit():
            number = int(lowered[1:])
            if 1 <= number <= 24:
                return f"F{number}"
        return cls._KEY_MAP.get(lowered, value)

    @classmethod
    def _map_button(cls, key: str) -> str:
        button = str(key).lower()
        if button not in cls._BUTTONS:
            raise ValueError(f"unsupported mouse button: {key}")
        return button

    @staticmethod
    def _delay_ms(seconds: float) -> float:
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError("down_time must be a finite non-negative number")
        return float(seconds) * 1000.0

    @staticmethod
    def _validate_size(name: str, value: tuple[int, int]) -> None:
        if len(value) != 2 or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in value
        ):
            raise ValueError(f"{name} must contain two positive integers")
