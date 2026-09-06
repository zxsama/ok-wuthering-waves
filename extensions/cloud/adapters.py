"""Interfaces implemented by the real browser integration and test doubles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .models import CloudPageState, CloudSessionState, QueueKind


class CloudPageAdapter(Protocol):
    def open(self, *, url: str, profile_dir: Path, visible: bool) -> None:
        """Open the site using the supplied persistent browser profile."""

    def observe(self) -> CloudPageState:
        """Return a stable semantic state, not a raw selector result."""

    def request_game(self) -> None:
        """Request entry from the authenticated cloud-game home page."""

    def select_queue(self, queue: QueueKind) -> None:
        """Select a queue when the page requests a choice."""

    def enter_game(self) -> None:
        """Confirm entry once queueing has completed."""

    def call_page(self, callback: Callable[[Any], Any]) -> Any:
        """Run a callback on the thread that owns the browser page."""

    def close(self) -> None:
        """Close the browser cleanly so profile changes are flushed."""


class SessionObserver(Protocol):
    def __call__(self, state: CloudSessionState, page_state: CloudPageState | None) -> None:
        """Receive state changes for logging, diagnostics, or notifications."""
