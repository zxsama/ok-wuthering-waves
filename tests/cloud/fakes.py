from __future__ import annotations

from collections import deque
from pathlib import Path

from extensions.cloud.models import CloudPageError, CloudPageState, QueueKind


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakePageAdapter:
    def __init__(self, states: list[CloudPageState | CloudPageError]) -> None:
        self.states = deque(states)
        self.current = states[-1]
        self.open_args: dict[str, object] | None = None
        self.actions: list[object] = []

    def open(self, *, url: str, profile_dir: Path, visible: bool) -> None:
        self.open_args = {"url": url, "profile_dir": profile_dir, "visible": visible}

    def observe(self) -> CloudPageState:
        if self.states:
            self.current = self.states.popleft()
        if isinstance(self.current, CloudPageError):
            raise self.current
        return self.current

    def request_game(self) -> None:
        self.actions.append("request_game")

    def select_queue(self, queue: QueueKind) -> None:
        self.actions.append(("select_queue", queue))

    def enter_game(self) -> None:
        self.actions.append("enter_game")

    def close(self) -> None:
        self.actions.append("close")
