"""Shared state and error types for the cloud-game runner."""

from __future__ import annotations

from enum import StrEnum


class QueueKind(StrEnum):
    NORMAL = "normal"
    FAST = "fast"


class CloudPageState(StrEnum):
    """Stable page-level observations supplied by a browser adapter.

    Selectors and visual-recognition details intentionally do not live here.
    They will be learned from real sessions and isolated in an adapter.
    """

    UNKNOWN = "unknown"
    LANDING = "landing"
    LOGIN_REQUIRED = "login_required"
    HOME = "home"
    QUEUE_SELECTION = "queue_selection"
    QUEUEING = "queueing"
    READY_TO_ENTER = "ready_to_enter"
    LAUNCHING = "launching"
    IN_GAME = "in_game"
    ERROR = "error"
    CLOSED = "closed"


class CloudSessionState(StrEnum):
    NEW = "new"
    OPENING = "opening"
    WAITING_MANUAL_LOGIN = "waiting_manual_login"
    AUTHENTICATED = "authenticated"
    REQUESTING_GAME = "requesting_game"
    SELECTING_QUEUE = "selecting_queue"
    QUEUEING = "queueing"
    ENTERING_GAME = "entering_game"
    IN_GAME = "in_game"
    EXITING_GAME = "exiting_game"
    CLOSED = "closed"
    FAILED = "failed"


class CloudRunnerError(RuntimeError):
    """Base class for expected cloud-runner failures."""


class CloudConfigurationError(CloudRunnerError):
    pass


class AuthenticationRequiredError(CloudRunnerError):
    pass


class CloudStateTimeoutError(CloudRunnerError):
    pass


class CloudPageError(CloudRunnerError):
    pass
