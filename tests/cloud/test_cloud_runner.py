from __future__ import annotations

import json

import pytest

from extensions.cloud.config import DEFAULT_CLOUD_URL, CloudSettings
from extensions.cloud.models import (
    AuthenticationRequiredError,
    CloudPageError,
    CloudPageState,
    CloudSessionState,
    CloudStateTimeoutError,
    QueueKind,
)
from extensions.cloud.profile import ProfileStore
from extensions.cloud.runner import CloudRunCoordinator
from extensions.cloud.scheduler import SchedulerAlreadyRunning, SingleInstanceFileLock
from extensions.cloud.session import CloudSession
from tests.cloud.fakes import FakeClock, FakePageAdapter


def make_session(tmp_path, states, *, enrolled=False):
    settings = CloudSettings(
        data_dir=tmp_path,
        poll_interval_seconds=1,
        login_timeout_seconds=10,
        interaction_timeout_seconds=10,
        queue_timeout_seconds=10,
        launch_timeout_seconds=10,
    )
    profile = ProfileStore(tmp_path)
    if enrolled:
        profile.mark_enrolled(settings.cloud_url)
    adapter = FakePageAdapter(states)
    clock = FakeClock()
    session = CloudSession(settings, profile, adapter, clock=clock, sleeper=clock.sleep)
    return settings, profile, adapter, session


def test_start_game_reopens_externally_closed_browser(tmp_path):
    _, _, adapter, session = make_session(tmp_path, [CloudPageState.IN_GAME])
    session.start_game(allow_manual_login=True)
    adapter.current = CloudPageState.CLOSED
    original_open = adapter.open

    def reopen(**kwargs):
        original_open(**kwargs)
        adapter.current = CloudPageState.IN_GAME

    adapter.open = reopen
    session.start_game(allow_manual_login=True)

    assert adapter.actions == ["close"]
    assert session.state == CloudSessionState.IN_GAME


def test_failed_browser_launch_cleans_partial_session_and_can_retry(tmp_path):
    _, _, adapter, session = make_session(tmp_path, [CloudPageState.IN_GAME])
    original_open = adapter.open

    def fail(**kwargs):
        raise CloudPageError("launch interrupted")

    adapter.open = fail
    with pytest.raises(CloudPageError, match="launch interrupted"):
        session.start_game(allow_manual_login=True)
    assert adapter.actions == ["close"]
    assert session.state == CloudSessionState.CLOSED
    adapter.open = original_open
    session.start_game(allow_manual_login=True)
    assert session.state == CloudSessionState.IN_GAME


def test_enroll_waits_for_manual_login_and_marks_profile(tmp_path):
    settings, profile, adapter, session = make_session(
        tmp_path,
        [
            CloudPageState.LANDING,
            CloudPageState.LOGIN_REQUIRED,
            CloudPageState.LOGIN_REQUIRED,
            CloudPageState.HOME,
        ],
    )

    session.enroll()

    assert session.state == CloudSessionState.AUTHENTICATED
    assert profile.is_enrolled(settings.cloud_url)
    assert adapter.actions == ["request_game"]
    assert adapter.open_args == {
        "url": settings.cloud_url,
        "profile_dir": profile.browser_profile_dir,
        "visible": True,
    }
    marker = json.loads(profile.enrollment_marker.read_text(encoding="utf-8"))
    assert marker["schema"] == 1
    assert "cookie" not in marker


def test_enroll_rejects_concurrent_execution_for_same_data_dir(tmp_path):
    _, profile, adapter, session = make_session(tmp_path, [CloudPageState.HOME])

    with SingleInstanceFileLock(tmp_path / "execution.lock"):
        with pytest.raises(SchedulerAlreadyRunning):
            CloudRunCoordinator(session, profile, lambda: None).enroll()

    assert adapter.actions == []


def test_landing_page_never_marks_profile_as_enrolled(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path,
        [CloudPageState.LANDING, CloudPageState.ERROR],
    )

    with pytest.raises(CloudPageError):
        session.enroll()

    assert not profile.is_enrolled(DEFAULT_CLOUD_URL)
    assert adapter.actions == ["request_game"]


def test_enrollment_marker_is_scoped_to_cloud_url(tmp_path):
    profile = ProfileStore(tmp_path)
    profile.mark_enrolled("https://cloud.example.test/primary/")

    assert profile.is_enrolled("https://cloud.example.test/primary/")
    assert not profile.is_enrolled("https://cloud.example.test/other/")


def test_mismatched_cloud_url_uses_first_run_manual_login_boundary(tmp_path):
    settings, profile, adapter, session = make_session(
        tmp_path,
        [CloudPageState.LOGIN_REQUIRED, CloudPageState.HOME, CloudPageState.IN_GAME],
        enrolled=False,
    )
    profile.mark_enrolled("https://cloud.example.test/other/")

    CloudRunCoordinator(session, profile, lambda: None).run_once()

    assert adapter.open_args["visible"] is True
    assert profile.is_enrolled(settings.cloud_url)


def test_run_once_drives_queue_then_always_exits_and_closes(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path,
        [
            CloudPageState.HOME,
            CloudPageState.QUEUE_SELECTION,
            CloudPageState.QUEUEING,
            CloudPageState.READY_TO_ENTER,
            CloudPageState.LAUNCHING,
            CloudPageState.IN_GAME,
        ],
        enrolled=True,
    )
    tasks = []

    CloudRunCoordinator(session, profile, lambda: tasks.append("task")).run_once()

    assert tasks == ["task"]
    assert adapter.actions == [
        "request_game",
        ("select_queue", QueueKind.NORMAL),
        "enter_game",
        "close",
    ]
    assert session.state == CloudSessionState.CLOSED


def test_run_once_passes_active_adapter_to_bridge_aware_task_runner(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path, [CloudPageState.IN_GAME], enrolled=True
    )
    received = []

    CloudRunCoordinator(
        session,
        profile,
        lambda active_adapter: received.append(active_adapter),
    ).run_once()

    assert received == [adapter]
    assert adapter.actions == ["close"]


def test_run_once_rejects_concurrent_execution_for_same_data_dir(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path, [CloudPageState.IN_GAME], enrolled=True
    )

    with SingleInstanceFileLock(tmp_path / "execution.lock"):
        with pytest.raises(SchedulerAlreadyRunning):
            CloudRunCoordinator(session, profile, lambda: None).run_once()

    assert adapter.actions == []


def test_existing_profile_does_not_wait_for_manual_login(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path, [CloudPageState.LOGIN_REQUIRED], enrolled=True
    )

    with pytest.raises(AuthenticationRequiredError):
        CloudRunCoordinator(session, profile, lambda: None).run_once()

    assert adapter.actions == ["close"]
    assert session.state == CloudSessionState.CLOSED


@pytest.mark.parametrize(
    "states",
    [
        [CloudPageState.UNKNOWN],
        [CloudPageState.ERROR],
        [CloudPageState.CLOSED],
        [CloudPageError("observation failed")],
    ],
)
def test_existing_profile_wraps_pre_authentication_failures(tmp_path, states):
    _, profile, adapter, session = make_session(tmp_path, states, enrolled=True)

    with pytest.raises(AuthenticationRequiredError) as captured:
        CloudRunCoordinator(session, profile, lambda: None).run_once()

    assert captured.value.__cause__ is not None
    assert adapter.actions == ["close"]
    assert session.state == CloudSessionState.CLOSED


def test_first_run_unknown_timeout_remains_manual_login_failure(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path, [CloudPageState.UNKNOWN], enrolled=False
    )

    with pytest.raises(CloudStateTimeoutError):
        CloudRunCoordinator(session, profile, lambda: None).run_once()

    assert adapter.actions == ["close"]


@pytest.mark.parametrize(
    ("states", "expected_error"),
    [
        ([CloudPageState.HOME, CloudPageState.ERROR], CloudPageError),
        ([CloudPageState.HOME, CloudPageState.QUEUEING], CloudStateTimeoutError),
        ([CloudPageState.HOME, CloudPageState.LAUNCHING], CloudStateTimeoutError),
    ],
)
def test_post_authentication_failures_are_not_login_failures(
    tmp_path, states, expected_error
):
    _, profile, _, session = make_session(tmp_path, states, enrolled=True)

    with pytest.raises(expected_error):
        CloudRunCoordinator(session, profile, lambda: None).run_once()


def test_task_failure_still_exits_game_and_closes_browser(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path, [CloudPageState.IN_GAME], enrolled=True
    )

    def fail_task():
        raise RuntimeError("task failed")

    with pytest.raises(RuntimeError, match="task failed"):
        CloudRunCoordinator(session, profile, fail_task).run_once()

    assert adapter.actions == ["close"]
    assert session.state == CloudSessionState.CLOSED


def test_interrupted_queue_closes_browser_without_page_actions(tmp_path):
    _, _, adapter, session = make_session(
        tmp_path, [CloudPageState.QUEUEING], enrolled=True
    )
    session.open()
    session._set_state(CloudSessionState.QUEUEING)

    session.stop()

    assert adapter.actions == ["close"]


def test_first_run_can_complete_manual_login_and_continue(tmp_path):
    _, profile, adapter, session = make_session(
        tmp_path,
        [
            CloudPageState.LANDING,
            CloudPageState.LOGIN_REQUIRED,
            CloudPageState.HOME,
            CloudPageState.QUEUE_SELECTION,
            CloudPageState.IN_GAME,
        ],
    )

    CloudRunCoordinator(session, profile, lambda: None).run_once()

    assert profile.is_enrolled(DEFAULT_CLOUD_URL)
    assert adapter.open_args["visible"] is True
    assert adapter.actions[0] == "request_game"
    assert adapter.actions[-1:] == ["close"]
