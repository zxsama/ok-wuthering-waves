from types import SimpleNamespace

import pytest

from extensions.cloud.config import CloudSettings
from extensions.cloud.models import AuthenticationRequiredError, CloudConfigurationError
from extensions.cloud.web_service import (
    CHARACTER_CODE_WEB_TAB,
    CloudWebController,
    _cloud_update_status,
    _disable_cloud_update_checks,
    _inject_default_web_language,
    _running_version,
    _web_translation_catalog,
    build_cloud_web_config,
)


class FakeSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback

    def disconnect(self, callback):
        assert callback == self.callback
        self.callback = None


class FakeProfile:
    def __init__(self, enrolled):
        self.enrolled = enrolled

    def is_enrolled(self, url):
        assert url == "https://cloud.invalid/"
        return self.enrolled


class FakeSession:
    def __init__(self, error=None):
        self.settings = SimpleNamespace(cloud_url="https://cloud.invalid/")
        self.error = error
        self.starts = []
        self.stops = 0
        self.state = "closed"

    def start_game(self, *, allow_manual_login):
        self.starts.append(allow_manual_login)
        if self.error:
            raise self.error
        self.state = "in_game"

    def stop(self):
        self.stops += 1
        self.state = "closed"


def make_runtime(task):
    starts_runtime = []
    starts = []
    actions = []
    stops = []
    closes = []
    runtime = SimpleNamespace()
    runtime.ok = SimpleNamespace(get_task=lambda identifier: (task, False))
    runtime.start = lambda: starts_runtime.append(True) or True
    runtime.start_task = lambda identifier: starts.append(identifier) or {"name": identifier}
    runtime.task_action = lambda identifier, action: actions.append((identifier, action)) or {}
    runtime.stop_task = lambda: stops.append(True) or {}
    runtime.close = lambda: closes.append(True)
    return runtime, starts_runtime, starts, actions, stops, closes


def test_web_config_keeps_upstream_tasks_and_adds_character_team_tab(tmp_path):
    source = {
        "gui": {"type": "qt"},
        "gui_icon": "icons/icon.png",
        "update_pyappify": {"to_version": "1.2.3"},
        "onetime_tasks": [["m", "T"]],
        "web_tabs": [["upstream.module", "ExistingTab"]],
    }

    result = build_cloud_web_config(source, CloudSettings(data_dir=tmp_path))

    assert result["onetime_tasks"] == [["m", "T"]]
    assert result["web_runtime"] is True
    assert result["locale"] == "zh_CN"
    assert result["web_tabs"] == [
        ["upstream.module", "ExistingTab"],
        CHARACTER_CODE_WEB_TAB,
    ]
    assert result["config_folder"] == str(tmp_path / "configs")
    assert "gui" not in result
    assert "gui_icon" not in result
    assert "update_pyappify" not in result


def test_cloud_update_status_disables_desktop_update_checks():
    assert _cloud_update_status({"version": "v1.0.2"}) == {
        "current_version": "v1.0.2",
        "versions": [],
        "update_available": False,
    }


def test_cloud_runtime_reports_updates_as_unsupported():
    runtime = SimpleNamespace(
        about=lambda: {"update_supported": True, "name": "OK-WW"},
        check_for_updates=lambda release_only=True: pytest.fail(
            "desktop update checker must not run in Docker"
        ),
    )

    _disable_cloud_update_checks(runtime, {"version": "v1.0.2"})

    assert runtime.about() == {"update_supported": False, "name": "OK-WW"}
    assert runtime.check_for_updates() == {
        "current_version": "v1.0.2",
        "versions": [],
        "update_available": False,
    }


def test_running_version_prefers_container_build_metadata(monkeypatch):
    monkeypatch.setenv("OK_WW_BUILD_VERSION", "v9.8.7")

    assert _running_version({"version": "dev"}) == "v9.8.7"


def test_running_version_uses_packaged_release_when_build_metadata_is_dev(monkeypatch):
    monkeypatch.setenv("OK_WW_BUILD_VERSION", "dev")

    assert _running_version({"version": "dev"}) == "v1.0.7"


def test_cloud_game_button_does_not_cover_header_actions():
    result = _inject_default_web_language(
        '<html><script type="module" src="/static/app.js"></script></html>',
        "zh_CN",
    )

    assert "right:20px;bottom:20px" in result
    assert "right:20px;top:14px" not in result


def test_docker_update_control_is_next_to_cloud_button_with_overlay_version():
    result = _inject_default_web_language(
        '<html><script type="module" src="/static/app.js"></script></html>',
        "zh_CN",
    )

    assert "okww-cloud-controls" in result
    assert "okww-docker-update" in result
    assert "更新 Git / Docker" in result
    assert "okww-docker-version" in result
    assert "position:absolute;right:0;top:calc(100% + 3px);opacity:.55" in result
    assert "headers:{'X-OK-WW-Update':'1'}" in result
    assert "busy||state.task_running||state.game_running" in result


def test_default_web_language_bootstrap_preserves_saved_choice():
    result = _inject_default_web_language(
        '<html><script type="module" src="/static/app.js"></script></html>',
        "zh_CN",
    )

    assert result.index("ok-script-language") < result.index('/static/app.js')
    assert "===null" in result
    assert "zh_CN" in result
    assert "Create Team" in result
    assert "创建队伍" in result
    assert "MutationObserver" in result
    assert "zh-hans" in result
    assert "WeakMap" not in result
    assert "characterData:true" not in result
    assert "okwwReverseTranslations" not in result
    assert "okww-cloud-game-control" in result
    assert "启动云游戏" in result


def test_english_default_still_embeds_chinese_catalog_for_later_switch():
    result = _inject_default_web_language(
        '<html><script type="module" src="/static/app.js"></script></html>',
        "en_US",
    )

    assert "localStorage.setItem('ok-script-language','en_US')" in result
    assert "创建队伍" in result


def test_web_translation_catalog_uses_project_gettext_without_changing_identifiers():
    catalog = _web_translation_catalog("zh_CN")

    assert catalog["📅 Daily Task"] == "📅 每日任务"
    assert catalog["Create Team"] == "创建队伍"
    assert catalog["Daily"] == "每天"
    assert _web_translation_catalog("en_US") == {}


def test_default_web_language_rejects_unknown_value():
    with pytest.raises(CloudConfigurationError, match="OK_WW_WEB_DEFAULT_LANGUAGE"):
        _inject_default_web_language('<script type="module"></script>', "invalid")


def test_first_manual_start_allows_enrollment_then_closes_only_game_on_done():
    task = object()
    runtime, _runtime_starts, starts, _actions, _stops, closes = make_runtime(task)
    session = FakeSession()
    signal = FakeSignal()
    controller = CloudWebController(
        runtime, session, FakeProfile(False), task_done_signal=signal
    )
    controller.install()

    assert runtime.start_task("DailyTask") == {"name": "DailyTask"}
    assert session.starts == [True]
    signal.callback(task)

    assert starts == ["DailyTask"]
    assert session.stops == 1
    assert closes == []


def test_failed_task_state_releases_cloud_game_and_allows_next_task():
    task = SimpleNamespace(running=False)
    runtime, _runtime_starts, starts, _actions, _stops, _closes = make_runtime(task)
    session = FakeSession()
    task_state = FakeSignal()
    controller = CloudWebController(
        runtime,
        session,
        FakeProfile(True),
        task_state_signal=task_state,
    )
    controller.install()

    runtime.start_task("DailyTask")
    task.running = True
    task_state.callback(task)
    task.running = False
    task_state.callback(None)

    assert controller.manual_game_status()["task_running"] is False
    assert session.stops == 1
    runtime.start_task("DailyTask")
    assert starts == ["DailyTask", "DailyTask"]


def test_queued_task_ignores_unrelated_idle_state_before_starting():
    task = SimpleNamespace(running=False)
    runtime, *_ = make_runtime(task)
    session = FakeSession()
    task_state = FakeSignal()
    controller = CloudWebController(
        runtime,
        session,
        FakeProfile(True),
        task_state_signal=task_state,
    )
    controller.install()

    runtime.start_task("DailyTask")
    task_state.callback(None)

    assert controller.manual_game_status()["task_running"] is True
    assert session.stops == 0


def test_success_signal_and_followup_idle_state_close_game_only_once():
    task = SimpleNamespace(running=True)
    runtime, *_ = make_runtime(task)
    session = FakeSession()
    task_done = FakeSignal()
    task_state = FakeSignal()
    controller = CloudWebController(
        runtime,
        session,
        FakeProfile(True),
        task_done_signal=task_done,
        task_state_signal=task_state,
    )
    controller.install()

    runtime.start_task("DailyTask")
    task_state.callback(task)
    task_done.callback(task)
    task.running = False
    task_state.callback(task)

    assert session.stops == 1


def test_controller_close_disconnects_both_task_signals():
    runtime, *_ = make_runtime(object())
    task_done = FakeSignal()
    task_state = FakeSignal()
    controller = CloudWebController(
        runtime,
        FakeSession(),
        FakeProfile(True),
        task_done_signal=task_done,
        task_state_signal=task_state,
    )

    controller.close()

    assert task_done.callback is None
    assert task_state.callback is None


def test_saved_login_failure_sends_notification_and_closes_game_page():
    error = AuthenticationRequiredError("expired")
    runtime, _runtime_starts, _starts, _actions, _stops, _closes = make_runtime(object())
    session = FakeSession(error)
    notifications = []
    controller = CloudWebController(
        runtime,
        session,
        FakeProfile(True),
        failure_notifier=notifications.append,
    )
    controller.install()

    with pytest.raises(AuthenticationRequiredError):
        runtime.start_task("DailyTask")

    assert session.starts == [False]
    assert notifications == [error]
    assert session.stops == 1


def test_scheduled_start_never_waits_for_manual_login():
    runtime, _runtime_starts, starts, _actions, _stops, _closes = make_runtime(object())
    session = FakeSession()
    controller = CloudWebController(runtime, session, FakeProfile(False))

    request = SimpleNamespace(task_id="src.task.DailyTask.DailyTask")
    controller.enqueue_scheduled(request)

    assert session.starts == [False]
    assert starts == ["src.task.DailyTask.DailyTask"]


def test_second_task_is_rejected_until_first_finishes():
    runtime, _runtime_starts, _starts, _actions, _stops, _closes = make_runtime(object())
    controller = CloudWebController(runtime, FakeSession(), FakeProfile(False))
    controller.start_task("DailyTask")

    with pytest.raises(RuntimeError, match="already running"):
        controller.start_task("ForgeryTask")


def test_stopping_a_task_closes_only_the_game_session():
    task = object()
    runtime, _runtime_starts, _starts, actions, stops, closes = make_runtime(task)
    session = FakeSession()
    controller = CloudWebController(runtime, session, FakeProfile(False))
    controller.install()
    runtime.start_task("DailyTask")

    runtime.task_action("DailyTask", "stop")

    assert actions == [("DailyTask", "stop")]
    assert session.stops == 1
    assert closes == []
    runtime.start_task("DailyTask")
    runtime.stop_task()
    assert stops == [True]
    assert session.stops == 2


def test_runtime_lifecycle_starts_and_stops_schedule_manager():
    runtime, runtime_starts, _starts, _actions, _stops, closes = make_runtime(object())
    controller = CloudWebController(runtime, FakeSession(), FakeProfile(False))
    schedule = SimpleNamespace(starts=0, stops=0)
    schedule.start = lambda: setattr(schedule, "starts", schedule.starts + 1)
    schedule.stop = lambda: setattr(schedule, "stops", schedule.stops + 1)
    controller.schedule_manager = schedule
    controller.install()

    assert runtime.start() is True
    runtime.close()
    assert runtime_starts == [True]
    assert schedule.starts == 1
    assert schedule.stops == 1
    assert closes == [True]


def test_manual_cloud_game_start_and_stop():
    runtime, *_ = make_runtime(object())
    session = FakeSession()
    controller = CloudWebController(runtime, session, FakeProfile(False))

    starting = controller.start_game_manually()
    controller._manual_game_thread.join(timeout=1)
    running = controller.manual_game_status()
    stopped = controller.stop_game_manually()

    assert starting["running"] is True
    assert session.starts == [True]
    assert running["running"] is True
    assert stopped["running"] is False
    assert session.stops == 1


def test_manual_cloud_game_stop_interrupts_active_task():
    task = object()
    runtime, _runtime_starts, starts, _actions, stops, _closes = make_runtime(task)
    session = FakeSession()
    controller = CloudWebController(runtime, session, FakeProfile(False))
    controller.install()
    runtime.start_task("ForgeryTask")

    stopped = controller.stop_game_manually()

    assert starts == ["ForgeryTask"]
    assert stops == [True]
    assert session.stops == 1
    assert stopped["running"] is False
    assert stopped["task_running"] is False


def test_manual_cloud_game_reports_stopped_while_start_thread_exits():
    runtime, *_ = make_runtime(object())
    session = FakeSession()
    controller = CloudWebController(runtime, session, FakeProfile(False))
    controller._manual_game_stop_requested = True
    controller._manual_game_thread = SimpleNamespace(is_alive=lambda: True)

    status = controller.manual_game_status()

    assert status["running"] is False
    assert status["busy"] is False
