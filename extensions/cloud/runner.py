"""One-shot orchestration around an existing task runner."""

from __future__ import annotations

import inspect
from collections.abc import Callable

from .models import AuthenticationRequiredError
from .profile import ProfileStore
from .scheduler import SingleInstanceFileLock
from .session import CloudSession


class CloudRunCoordinator:
    def __init__(
        self,
        session: CloudSession,
        profile: ProfileStore,
        task_runner: Callable[..., None],
        failure_notifier: Callable[[Exception], None] | None = None,
    ) -> None:
        self.session = session
        self.profile = profile
        self.task_runner = task_runner
        self.failure_notifier = failure_notifier

    def enroll(self) -> None:
        with SingleInstanceFileLock(self.profile.root / "execution.lock"):
            try:
                self.session.enroll()
            finally:
                self.session.stop()

    def run_once(self) -> None:
        with SingleInstanceFileLock(self.profile.root / "execution.lock"):
            first_run = not self.profile.is_enrolled(self.session.settings.cloud_url)
            try:
                self.session.start_game(allow_manual_login=first_run)
                self._run_task()
            except AuthenticationRequiredError as exc:
                if self.failure_notifier is not None:
                    try:
                        self.failure_notifier(exc)
                    except Exception as notification_error:
                        exc.add_note(
                            "SMTP notification also failed: "
                            f"{type(notification_error).__name__}: {notification_error}"
                        )
                raise
            finally:
                self.session.stop()

    def _run_task(self) -> None:
        """Pass the active page adapter to bridge-aware task runners.

        Existing no-argument runner factories remain supported so downstream
        integrations do not need to change until they opt into the cloud page
        bridge.
        """

        try:
            parameters = inspect.signature(self.task_runner).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        accepts_adapter = any(
            parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
            for parameter in parameters
        ) or any(parameter.kind == parameter.VAR_POSITIONAL for parameter in parameters)
        if accepts_adapter:
            self.task_runner(self.session.adapter)
        else:
            self.task_runner()
