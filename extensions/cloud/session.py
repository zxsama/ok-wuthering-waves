"""Cloud-game lifecycle state machine independent of browser implementation."""

from __future__ import annotations

import time
from collections.abc import Callable

from .adapters import CloudPageAdapter, SessionObserver
from .config import CloudSettings
from .models import (
    AuthenticationRequiredError,
    CloudPageError,
    CloudPageState,
    CloudSessionState,
    CloudStateTimeoutError,
)
from .profile import ProfileStore


_AUTHENTICATED_STATES = frozenset(
    {
        CloudPageState.HOME,
        CloudPageState.QUEUE_SELECTION,
        CloudPageState.QUEUEING,
        CloudPageState.READY_TO_ENTER,
        CloudPageState.LAUNCHING,
        CloudPageState.IN_GAME,
    }
)


class CloudSession:
    def __init__(
        self,
        settings: CloudSettings,
        profile: ProfileStore,
        adapter: CloudPageAdapter,
        *,
        observer: SessionObserver | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.profile = profile
        self.adapter = adapter
        self.observer = observer
        self.clock = clock
        self.sleeper = sleeper
        self.state = CloudSessionState.NEW
        self._opened = False

    def _set_state(
        self, state: CloudSessionState, page_state: CloudPageState | None = None
    ) -> None:
        if self.state == state:
            return
        self.state = state
        if self.observer is not None:
            self.observer(state, page_state)

    def open(self, *, force_visible: bool = False) -> None:
        self._set_state(CloudSessionState.OPENING)
        self.adapter.open(
            url=self.settings.cloud_url,
            profile_dir=self.profile.prepare(),
            visible=force_visible or self.settings.browser_visible,
        )
        self._opened = True

    def enroll(self) -> None:
        """Wait for a human to complete login, then persist an enrollment marker."""

        if not self._opened:
            self.open(force_visible=True)
        deadline = self.clock() + self.settings.login_timeout_seconds
        landing_requested = False
        while True:
            page_state = self.adapter.observe()
            if page_state in _AUTHENTICATED_STATES:
                self.profile.mark_enrolled(self.settings.cloud_url)
                self._set_state(CloudSessionState.AUTHENTICATED, page_state)
                return
            if page_state == CloudPageState.LANDING:
                self._set_state(CloudSessionState.REQUESTING_GAME, page_state)
                if not landing_requested:
                    self.adapter.request_game()
                    landing_requested = True
            elif page_state == CloudPageState.LOGIN_REQUIRED:
                self._set_state(CloudSessionState.WAITING_MANUAL_LOGIN, page_state)
            elif page_state in {CloudPageState.ERROR, CloudPageState.CLOSED}:
                self._fail(CloudPageError(f"cannot enroll from page state: {page_state}"))
            if self.clock() >= deadline:
                self._fail(CloudStateTimeoutError("manual cloud login timed out"))
            self.sleeper(self.settings.poll_interval_seconds)

    def start_game(self, *, allow_manual_login: bool) -> None:
        if not self._opened:
            self.open(force_visible=allow_manual_login)

        last_page_state: CloudPageState | None = None
        deadline = self.clock() + self.settings.interaction_timeout_seconds
        acted_state: CloudPageState | None = None
        authentication_restored = False

        while True:
            try:
                page_state = self.adapter.observe()
            except CloudPageError as exc:
                self._fail_for_phase(
                    exc,
                    allow_manual_login=allow_manual_login,
                    authentication_restored=authentication_restored,
                )
            if page_state != last_page_state:
                deadline = self.clock() + self._timeout_for(
                    page_state, allow_manual_login=allow_manual_login
                )
                acted_state = None
                last_page_state = page_state

            if page_state in _AUTHENTICATED_STATES:
                authentication_restored = True
                if not self.profile.is_enrolled(self.settings.cloud_url):
                    self.profile.mark_enrolled(self.settings.cloud_url)

            if page_state == CloudPageState.LANDING:
                self._set_state(CloudSessionState.REQUESTING_GAME, page_state)
                if acted_state != page_state:
                    try:
                        self.adapter.request_game()
                    except CloudPageError as exc:
                        self._fail_for_phase(
                            exc,
                            allow_manual_login=allow_manual_login,
                            authentication_restored=authentication_restored,
                        )
                    acted_state = page_state
            elif page_state == CloudPageState.LOGIN_REQUIRED:
                if not allow_manual_login:
                    self._set_state(CloudSessionState.WAITING_MANUAL_LOGIN, page_state)
                    if self.clock() >= deadline:
                        self._fail(
                            AuthenticationRequiredError(
                                "stored cloud login is no longer valid"
                            )
                        )
                    self.sleeper(self.settings.poll_interval_seconds)
                    continue
                self._set_state(CloudSessionState.WAITING_MANUAL_LOGIN, page_state)
            elif page_state == CloudPageState.HOME:
                self._set_state(CloudSessionState.REQUESTING_GAME, page_state)
                if acted_state != page_state:
                    self.adapter.request_game()
                    acted_state = page_state
            elif page_state == CloudPageState.QUEUE_SELECTION:
                self._set_state(CloudSessionState.SELECTING_QUEUE, page_state)
                if acted_state != page_state:
                    self.adapter.select_queue(self.settings.queue)
                    acted_state = page_state
            elif page_state == CloudPageState.QUEUEING:
                self._set_state(CloudSessionState.QUEUEING, page_state)
            elif page_state == CloudPageState.READY_TO_ENTER:
                self._set_state(CloudSessionState.ENTERING_GAME, page_state)
                if acted_state != page_state:
                    self.adapter.enter_game()
                    acted_state = page_state
            elif page_state == CloudPageState.LAUNCHING:
                self._set_state(CloudSessionState.ENTERING_GAME, page_state)
            elif page_state == CloudPageState.IN_GAME:
                self._set_state(CloudSessionState.IN_GAME, page_state)
                return
            elif page_state in {CloudPageState.ERROR, CloudPageState.CLOSED}:
                self._fail_for_phase(
                    CloudPageError(f"cloud page entered terminal state: {page_state}"),
                    allow_manual_login=allow_manual_login,
                    authentication_restored=authentication_restored,
                )

            if self.clock() >= deadline:
                self._fail_for_phase(
                    CloudStateTimeoutError(
                        f"timed out in cloud page state: {page_state}"
                    ),
                    allow_manual_login=allow_manual_login,
                    authentication_restored=authentication_restored,
                )
            self.sleeper(self.settings.poll_interval_seconds)

    def stop(self) -> None:
        if not self._opened:
            return
        try:
            self.adapter.close()
        finally:
            self._opened = False
            self._set_state(CloudSessionState.CLOSED)

    def _timeout_for(
        self, page_state: CloudPageState, *, allow_manual_login: bool = True
    ) -> float:
        if page_state == CloudPageState.LOGIN_REQUIRED:
            return (
                self.settings.login_timeout_seconds
                if allow_manual_login
                else self.settings.session_restore_timeout_seconds
            )
        if page_state == CloudPageState.QUEUEING:
            return self.settings.queue_timeout_seconds
        if page_state in {CloudPageState.READY_TO_ENTER, CloudPageState.LAUNCHING}:
            return self.settings.launch_timeout_seconds
        return self.settings.interaction_timeout_seconds

    def _fail(self, error: Exception) -> None:
        self._set_state(CloudSessionState.FAILED)
        raise error

    def _fail_for_phase(
        self,
        error: CloudPageError | CloudStateTimeoutError,
        *,
        allow_manual_login: bool,
        authentication_restored: bool,
    ) -> None:
        if not allow_manual_login and not authentication_restored:
            self._set_state(CloudSessionState.FAILED)
            raise AuthenticationRequiredError(
                "stored cloud login could not be restored: "
                f"{type(error).__name__}: {error}"
            ) from error
        self._fail(error)
