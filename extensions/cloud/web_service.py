"""Persistent browser control plane for the cloud-game adapter.

The service reuses ok-script's own Web UI so task configuration continues to
follow upstream task metadata.  Only the device lifecycle and schedule backend
are replaced here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cloud_schedules import ScheduledTaskRequest
from .config import CloudSettings
from .models import AuthenticationRequiredError, CloudConfigurationError
from .notifier import SmtpFailureNotifier, SmtpSettings
from .ok_bridge import CloudDeviceManager, create_cloud_ok_class
from .profile import ProfileStore
from .session import CloudSession
from .task_runner import build_cloud_task_config


logger = logging.getLogger(__name__)
CHARACTER_CODE_WEB_TAB = [
    "extensions.cloud.character_code_task",
    "CloudCharacterCodeTask",
]
WEB_DEFAULT_LANGUAGE_ENV = "OK_WW_WEB_DEFAULT_LANGUAGE"
WEB_LANGUAGES = {"Auto", "en_US", "es_ES", "ja_JP", "ko_KR", "zh_CN", "zh_TW"}
ZH_CN_WEB_OVERRIDES = {
    "Auto Exit After Task": "任务完成后自动退出",
    "Character Config": "角色设置",
    "Cloud": "云端",
    "Custom": "自定义",
    "Daily": "每天",
    "Start Cloud Game": "启动云游戏",
    "Starting Cloud Game...": "正在启动云游戏…",
    "Stop Cloud Game": "关闭云游戏",
    "Game Hotkey": "游戏快捷键",
    "Monthly": "每月",
    "Once": "单次",
    "Ready": "就绪",
    "Release": "正式版",
    "Weekly": "每周",
}


def _web_translation_catalog(language: str) -> dict[str, str]:
    if language != "zh_CN":
        return {}
    try:
        from ok.core.translation import get_translations

        raw_catalog = getattr(get_translations(language), "_catalog", {})
        catalog = {
            key: value
            for key, value in raw_catalog.items()
            if isinstance(key, str)
            and isinstance(value, str)
            and key
            and value
            and key != value
        }
    except Exception:
        logger.exception("failed to load Web translation catalog for %s", language)
        catalog = {}
    catalog.update(ZH_CN_WEB_OVERRIDES)
    return catalog


def _inject_default_web_language(index_html: str, language: str) -> str:
    """Set the first-visit Web locale without overriding a saved preference."""

    if language not in WEB_LANGUAGES:
        raise CloudConfigurationError(
            f"{WEB_DEFAULT_LANGUAGE_ENV} must be one of {sorted(WEB_LANGUAGES)}"
        )
    marker = '<script type="module"'
    translations = json.dumps(
        _web_translation_catalog("zh_CN"), ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    bootstrap = (
        "<script>"
        "if(localStorage.getItem('ok-script-language')===null){"
        f"localStorage.setItem('ok-script-language',{language!r});"
        "}"
        f"const okwwTranslations={translations};"
        "function okwwIsChinese(){"
        "const locale=document.documentElement.lang.toLowerCase();"
        "return locale==='zh-cn'||locale.startsWith('zh-hans');"
        "}"
        "function okwwTranslateRoot(root){"
        "if(!okwwIsChinese())return;"
        "const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);"
        "let node;while((node=walker.nextNode())){"
        "if(['SCRIPT','STYLE','TEXTAREA'].includes(node.parentElement?.tagName))continue;"
        "const source=node.nodeValue;const trimmed=source.trim();"
        "const translated=okwwTranslations[trimmed];"
        "if(translated)node.nodeValue=source.replace(trimmed,translated);"
        "}"
        "}"
        "let okwwTranslationPending=false;"
        "const okwwPendingRoots=new Set();"
        "function okwwScheduleTranslation(root){"
        "if(root)okwwPendingRoots.add(root);"
        "if(okwwTranslationPending)return;okwwTranslationPending=true;"
        "queueMicrotask(()=>{"
        "okwwTranslationPending=false;"
        "if(!okwwIsChinese()){okwwPendingRoots.clear();return;}"
        "for(const pendingRoot of okwwPendingRoots)okwwTranslateRoot(pendingRoot);"
        "okwwPendingRoots.clear();"
        "});"
        "}"
        "const okwwObserver=new MutationObserver(records=>{"
        "for(const record of records){"
        "if(record.type==='attributes'){okwwScheduleTranslation(document.body);continue;}"
        "for(const addedNode of record.addedNodes){"
        "if(addedNode.nodeType===Node.TEXT_NODE){"
        "okwwScheduleTranslation(addedNode.parentNode);"
        "}else if(addedNode.nodeType===Node.ELEMENT_NODE){"
        "okwwScheduleTranslation(addedNode);"
        "}"
        "}"
        "}"
        "});"
        "okwwObserver.observe(document.documentElement,{subtree:true,childList:true,"
        "attributes:true,attributeFilter:['lang']});"
        "document.addEventListener('DOMContentLoaded',()=>okwwScheduleTranslation(document.body));"
        "async function okwwRefreshCloudGame(){"
        "const button=document.getElementById('okww-cloud-game-control');if(!button)return;"
        "try{const response=await fetch('/api/cloud-game');const state=await response.json();"
        "button.disabled=state.task_running&&!state.running;"
        "button.dataset.running=String(state.running);"
        "button.textContent=state.running?'Stop Cloud Game':"
        "(state.busy?'Starting Cloud Game...':'Start Cloud Game');"
        "button.title=state.error||state.state||'';okwwTranslateRoot(button);"
        "}catch(error){button.disabled=false;button.title=String(error);}}"
        "async function okwwToggleCloudGame(){"
        "const button=document.getElementById('okww-cloud-game-control');"
        "button.disabled=true;"
        "const action=button.dataset.running==='true'?'stop':'start';"
        "try{const response=await fetch('/api/cloud-game/'+action,{method:'POST'});"
        "if(!response.ok)throw new Error(await response.text());}finally{await okwwRefreshCloudGame();}"
        "}"
        "document.addEventListener('DOMContentLoaded',()=>{"
        "const button=document.createElement('button');button.id='okww-cloud-game-control';"
        "button.type='button';button.textContent='Start Cloud Game';"
        "button.style.cssText='position:fixed;right:20px;top:14px;z-index:2147483647;"
        "padding:8px 14px;border:0;border-radius:8px;background:#2563eb;color:white;"
        "font:600 14px sans-serif;cursor:pointer;box-shadow:0 2px 8px #0004';"
        "button.addEventListener('click',okwwToggleCloudGame);document.body.appendChild(button);"
        "okwwRefreshCloudGame();setInterval(okwwRefreshCloudGame,2000);"
        "});"
        "</script>"
    )
    if marker not in index_html:
        raise CloudConfigurationError("ok-script Web index has no module entrypoint")
    return index_html.replace(marker, bootstrap + "\n    " + marker, 1)


class CloudWebController:
    """Coordinate manual and scheduled starts through one cloud session."""

    def __init__(
        self,
        runtime: Any,
        session: CloudSession,
        profile: ProfileStore,
        *,
        failure_notifier: Callable[[Exception], None] | None = None,
        task_done_signal: Any = None,
    ) -> None:
        self.runtime = runtime
        self.session = session
        self.profile = profile
        self.failure_notifier = failure_notifier
        self._lock = threading.RLock()
        self._active_task: Any = None
        self._manual_game_thread: threading.Thread | None = None
        self._manual_game_error: str | None = None
        self._manual_game_stop_requested = False
        self.schedule_manager: Any = None
        self._original_start = runtime.start
        self._original_start_task = runtime.start_task
        self._original_task_action = runtime.task_action
        self._original_stop_task = runtime.stop_task
        self._original_close = runtime.close
        self._task_done_signal = task_done_signal
        if task_done_signal is not None:
            task_done_signal.connect(self._on_task_done)

    def install(self) -> None:
        self.runtime.start = self.start
        self.runtime.start_task = self.start_task
        self.runtime.task_action = self.task_action
        self.runtime.stop_task = self.stop_task
        self.runtime.close = self.close

    def start(self):
        result = self._original_start()
        if self.schedule_manager is not None:
            self.schedule_manager.start()
        return result

    def start_task(self, task_identifier: str, *, allow_manual_login: bool | None = None):
        with self._lock:
            if self._active_task is not None:
                raise RuntimeError("Another task is already running")
            task, _is_trigger = self.runtime.ok.get_task(task_identifier)
            self._active_task = task
            if allow_manual_login is None:
                allow_manual_login = not self.profile.is_enrolled(
                    self.session.settings.cloud_url
                )
            try:
                self.session.start_game(allow_manual_login=allow_manual_login)
                return self._original_start_task(task_identifier)
            except AuthenticationRequiredError as exc:
                self._notify_login_failure(exc)
                self._active_task = None
                self.session.stop()
                raise
            except BaseException:
                self._active_task = None
                self.session.stop()
                raise

    def enqueue_scheduled(self, request: ScheduledTaskRequest) -> object:
        return self.start_task(request.task_id, allow_manual_login=False)

    def manual_game_status(self) -> dict[str, object]:
        with self._lock:
            starting = (
                not self._manual_game_stop_requested
                and self._manual_game_thread is not None
                and self._manual_game_thread.is_alive()
            )
            state = str(self.session.state)
            running = starting or state not in {"new", "closed", "failed"}
            return {
                "state": state,
                "running": running,
                "busy": starting,
                "task_running": self._active_task is not None,
                "error": self._manual_game_error,
            }

    def start_game_manually(self) -> dict[str, object]:
        with self._lock:
            if self._active_task is not None:
                raise RuntimeError("An automation task is already running")
            if self._manual_game_thread is not None and self._manual_game_thread.is_alive():
                return self.manual_game_status()
            self._manual_game_error = None
            self._manual_game_stop_requested = False
            self._manual_game_thread = threading.Thread(
                target=self._run_manual_game_start,
                name="cloud-game-manual-start",
                daemon=True,
            )
            self._manual_game_thread.start()
        return self.manual_game_status()

    def _run_manual_game_start(self) -> None:
        try:
            self.session.start_game(allow_manual_login=True)
        except Exception as exc:
            with self._lock:
                if not self._manual_game_stop_requested:
                    logger.exception("manual cloud-game start failed")
                    self._manual_game_error = str(exc)

    def stop_game_manually(self) -> dict[str, object]:
        with self._lock:
            self._manual_game_stop_requested = True
            task_running = self._active_task is not None

        if task_running:
            self.stop_task()
        else:
            self.session.stop()

        with self._lock:
            self._manual_game_error = None
        return self.manual_game_status()

    def task_action(self, task_identifier: str, action: str):
        result = self._original_task_action(task_identifier, action)
        if action in {"disable", "stop"}:
            self._close_active_game(task_identifier)
        return result

    def stop_task(self):
        result = self._original_stop_task()
        self._close_active_game()
        return result

    def _close_active_game(self, task_identifier: str | None = None) -> None:
        with self._lock:
            if self._active_task is None:
                return
            if task_identifier is not None:
                task, _is_trigger = self.runtime.ok.get_task(task_identifier)
                if task is not self._active_task:
                    return
            self._active_task = None
        try:
            self.session.stop()
        except Exception:
            logger.exception("failed to close the cloud-game tab")

    def _notify_login_failure(self, error: AuthenticationRequiredError) -> None:
        if self.failure_notifier is None:
            return
        try:
            self.failure_notifier(error)
        except Exception:
            logger.exception("failed to send cloud login notification")

    def _on_task_done(self, task: Any) -> None:
        with self._lock:
            if task is not self._active_task:
                return
        self._close_active_game()

    def close(self) -> None:
        if self._task_done_signal is not None:
            try:
                self._task_done_signal.disconnect(self._on_task_done)
            except Exception:
                pass
        try:
            if self.schedule_manager is not None:
                self.schedule_manager.stop()
            self.session.stop()
        finally:
            self._original_close()


def build_cloud_web_config(source: dict[str, Any], settings: CloudSettings) -> dict[str, Any]:
    config = build_cloud_task_config(source)
    config["locale"] = os.environ.get(WEB_DEFAULT_LANGUAGE_ENV, "zh_CN").strip() or "zh_CN"
    config["web_runtime"] = True
    config["config_folder"] = str(settings.data_dir / "configs")
    web_tabs = list(config.get("web_tabs", []))
    if CHARACTER_CODE_WEB_TAB not in web_tabs:
        web_tabs.append(CHARACTER_CODE_WEB_TAB)
    config["web_tabs"] = web_tabs
    # The packaged ok-script static directory is read-only to the container
    # user.  Omitting the optional copied icon keeps the runtime immutable.
    config.pop("gui_icon", None)
    # Packaged/server builds have no pyappify application identity, so the
    # desktop updater cannot produce a meaningful version list.
    config.pop("update_pyappify", None)
    return config


def create_cloud_web_app(
    settings: CloudSettings,
    adapter: Any,
    *,
    schedule_manager_factory: Callable[[Path, Callable[[ScheduledTaskRequest], object]], Any]
    | None = None,
):
    """Build the shared Web UI without opening the game page at service startup."""

    try:
        from config import config as project_config
        from ok.core.events import communicate
        from ok.ui.web import app as ok_web_app_module
        from ok.ui.web.app import create_web_app
        from starlette.responses import HTMLResponse
    except ImportError as exc:
        raise CloudConfigurationError(
            "the cloud Web service requires ok-script web dependencies"
        ) from exc

    profile = ProfileStore(settings.data_dir)
    session = CloudSession(settings, profile, adapter)
    manager = CloudDeviceManager(adapter)
    CloudOK = create_cloud_ok_class(manager)
    ok_instance = CloudOK(build_cloud_web_config(project_config, settings))
    app = create_web_app(ok_instance.config, ok_instance=ok_instance)
    default_language = os.environ.get(WEB_DEFAULT_LANGUAGE_ENV, "zh_CN").strip() or "zh_CN"
    index_path = Path(ok_web_app_module.__file__).with_name("static") / "index.html"
    localized_index = _inject_default_web_language(
        index_path.read_text(encoding="utf-8"), default_language
    )

    @app.middleware("http")
    async def cloud_web_defaults(request, call_next):
        if request.method in {"GET", "HEAD"} and request.url.path == "/":
            return HTMLResponse(localized_index)
        return await call_next(request)

    runtime = app.state.runtime

    smtp_settings = SmtpSettings.from_env()
    controller = CloudWebController(
        runtime,
        session,
        profile,
        failure_notifier=(
            SmtpFailureNotifier(smtp_settings) if smtp_settings is not None else None
        ),
        task_done_signal=communicate.task_done,
    )
    controller.install()
    app.state.cloud_controller = controller

    if schedule_manager_factory is None:
        from .cloud_schedule_manager import CloudScheduleManager

        schedule_manager_factory = CloudScheduleManager
    schedule_manager = schedule_manager_factory(
        settings.data_dir / "schedules.json", controller.enqueue_scheduled
    )
    runtime._schedule_manager = schedule_manager
    controller.schedule_manager = schedule_manager
    app.state.cloud_schedule_manager = schedule_manager

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/api/cloud-game", include_in_schema=False)
    async def cloud_game_status():
        return controller.manual_game_status()

    @app.post("/api/cloud-game/start", include_in_schema=False)
    async def start_cloud_game():
        return controller.start_game_manually()

    @app.post("/api/cloud-game/stop", include_in_schema=False)
    async def stop_cloud_game():
        return controller.stop_game_manually()

    return app


def serve_cloud_web(
    settings: CloudSettings,
    adapter: Any,
    *,
    host: str,
    port: int,
) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise CloudConfigurationError(
            "the cloud Web service requires uvicorn"
        ) from exc
    app = create_cloud_web_app(settings, adapter)
    uvicorn.run(app, host=host, port=port, log_config=None)
