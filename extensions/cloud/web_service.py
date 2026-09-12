"""Persistent browser control plane for the cloud-game adapter.

The service reuses ok-script's own Web UI so task configuration continues to
follow upstream task metadata.  Only the device lifecycle and schedule backend
are replaced here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from types import MethodType
from typing import Any

from .cloud_schedules import ScheduledTaskRequest
from .config import CloudSettings
from .models import AuthenticationRequiredError, CloudConfigurationError
from .notifier import SmtpFailureNotifier, SmtpSettings
from .ok_bridge import CloudDeviceManager, create_cloud_ok_class
from .profile import ProfileStore
from .session import CloudSession
from .task_runner import build_cloud_task_config
from .update_control import DockerUpdateControl


logger = logging.getLogger(f"ok.{__name__}")
CHARACTER_CODE_WEB_TAB = [
    "extensions.cloud.character_code_task",
    "CloudCharacterCodeTask",
]
WEB_DEFAULT_LANGUAGE_ENV = "OK_WW_WEB_DEFAULT_LANGUAGE"
WEB_LANGUAGES = {"Auto", "en_US", "es_ES", "ja_JP", "ko_KR", "zh_CN", "zh_TW"}


def _install_schedule_email_support(runtime: Any) -> None:
    from ok.util.windows_schedule import normalize_trigger_type, resolve_schedule_task_index

    def create_schedule_task(self, body: dict[str, Any]):
        task_index = int(body.get("task_index", 0))
        tasks = list(self.executor.onetime_tasks or [])
        available_indices = {
            index + 1
            for index, task in enumerate(tasks)
            if getattr(task, "support_schedule_task", False)
            and getattr(task, "visible", True)
        }
        if task_index not in available_indices:
            raise ValueError("Invalid scheduled task")
        task = tasks[task_index - 1]
        success = self.schedule_manager.create_task(
            task_name=str(body.get("name") or ""),
            task_index=task_index,
            trigger_type=normalize_trigger_type(body.get("trigger_type", "Daily")),
            timeout_hours=int(body.get("timeout_hours", 0)),
            start_hour=int(body.get("start_hour", 9)),
            start_minute=int(body.get("start_minute", 0)),
            auto_exit=bool(body.get("auto_exit", True)),
            email_report=bool(body.get("email_report", True)),
            enabled=True,
            interval_days=int(body.get("interval_days", 0)),
            interval_hours=int(body.get("interval_hours", 0)),
            task_identifier=f"{task.__class__.__module__}.{task.__class__.__name__}",
        )
        if not success:
            raise RuntimeError("Failed to create scheduled task")
        return self.schedule_tasks()

    def update_schedule_task(self, name: str, body: dict[str, Any]):
        current = self.schedule_manager.cache.get(name)
        if current is None:
            current = next(
                (
                    item
                    for item in self.schedule_manager.cache.values()
                    if item.path == name or item.name == name
                ),
                None,
            )
        if current is None or current.read_only:
            raise ValueError("Scheduled task is not editable")
        task_index = int(body.get("task_index", current.task_index))
        if current.task_identifier:
            task_index = resolve_schedule_task_index(
                current.task_identifier, self.executor.onetime_tasks
            )
        available_indices = {
            index + 1
            for index, task in enumerate(self.executor.onetime_tasks or [])
            if getattr(task, "support_schedule_task", False)
            and getattr(task, "visible", True)
        }
        if task_index not in available_indices:
            raise ValueError("Invalid scheduled task")
        task_identifier = getattr(current, "task_identifier", "") or None
        if not task_identifier:
            try:
                tasks = list(self.executor.onetime_tasks or [])
                if 1 <= task_index <= len(tasks):
                    task = tasks[task_index - 1]
                    task_identifier = (
                        f"{task.__class__.__module__}.{task.__class__.__name__}"
                    )
            except Exception:
                logger.exception(
                    "Failed to resolve task_identifier for modified scheduled task"
                )
        success = self.schedule_manager.replace_task(
            task_name=current.name,
            task_index=task_index,
            trigger_type=normalize_trigger_type(
                body.get("trigger_type", current.trigger_type or "Daily")
            ),
            timeout_hours=int(body.get("timeout_hours", 0)),
            start_hour=int(body.get("start_hour", 9)),
            start_minute=int(body.get("start_minute", 0)),
            auto_exit=bool(body.get("auto_exit", True)),
            email_report=bool(body.get("email_report", current.email_report)),
            enabled=current.enabled,
            description=current.description,
            interval_days=int(body.get("interval_days", current.interval_days)),
            interval_hours=int(body.get("interval_hours", current.interval_hours)),
            task_identifier=task_identifier,
        )
        if not success:
            raise RuntimeError("Failed to modify scheduled task")
        return self.schedule_tasks()

    runtime.create_schedule_task = MethodType(create_schedule_task, runtime)
    runtime.update_schedule_task = MethodType(update_schedule_task, runtime)


def _install_slash_safe_task_routes(app: Any, runtime: Any) -> None:
    """Add task endpoints whose names may contain a slash."""

    from fastapi import HTTPException

    @app.post("/api/tasks/{name:path}/start", include_in_schema=False)
    async def start_task_with_slash(name: str):
        try:
            return await asyncio.to_thread(runtime.start_task, name)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{name:path}/action", include_in_schema=False)
    async def task_action_with_slash(name: str, body: dict[str, Any]):
        try:
            return await asyncio.to_thread(
                runtime.task_action, name, str(body.get("action", ""))
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{name:path}/config/reset", include_in_schema=False)
    async def reset_task_config_with_slash(name: str):
        try:
            return await asyncio.to_thread(runtime.reset_task_config, name)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{name:path}/config", include_in_schema=False)
    async def set_task_config_with_slash(name: str, body: dict[str, Any]):
        try:
            return await asyncio.to_thread(
                runtime.set_task_config,
                name,
                str(body.get("key", "")),
                body.get("value"),
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


ZH_CN_WEB_OVERRIDES = {
    "Auto Exit After Task": "任务完成后自动退出",
    "Character Config": "角色设置",
    "Cloud": "云端",
    "Custom": "自定义",
    "Daily": "每天",
    "Start Cloud Game": "启动云游戏",
    "Starting Cloud Game...": "正在启动云游戏…",
    "Stop Cloud Game": "关闭云游戏",
    "Update Docker": "更新 Git / Docker",
    "Updating Docker...": "正在更新 Docker…",
    "Send Email Report After Task": "任务完成后发送邮件汇报",
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
        "let okwwEditingSchedulePath='';"
        "document.addEventListener('click',event=>{"
        "const button=event.target.closest?.('button');"
        "const row=button?.closest('.schedule-table tbody tr');if(!row)return;"
        "const actions=[...row.querySelectorAll('td:last-child button')];"
        "if(actions[0]!==button)return;"
        "okwwEditingSchedulePath=row.querySelector('td')?.getAttribute('title')||'';"
        "},{capture:true});"
        "function okwwEnhanceScheduleForms(root){"
        "for(const form of root.querySelectorAll?.('form.schedule-form')||[]){"
        "if(form.querySelector('[data-okww-email-report]'))continue;"
        "const checks=[...form.querySelectorAll('label.schedule-check')];"
        "const autoExit=checks.find(label=>label.querySelector('input[type=checkbox]'));"
        "if(!autoExit)continue;const label=document.createElement('label');"
        "label.className='schedule-check';label.dataset.okwwEmailReport='1';"
        "const input=document.createElement('input');input.type='checkbox';input.checked=true;"
        "label.append(input,document.createTextNode('Send Email Report After Task'));"
        "autoExit.after(label);okwwTranslateRoot(label);"
        "const disabled=form.querySelector('input[disabled]');"
        "if(disabled){const submit=form.querySelector('button[type=submit]');"
        "input.disabled=true;if(submit)submit.disabled=true;"
        "fetch('/api/schedule').then(response=>response.json()).then(data=>{"
        "const item=data.tasks.find(task=>task.path===okwwEditingSchedulePath||"
        "task.name===disabled.value||okwwTranslations[task.name]===disabled.value);"
        "if(item)input.checked=item.email_report!==false;}).catch(()=>{}).finally(()=>{"
        "input.disabled=false;if(submit)submit.disabled=false;});}"
        "form.addEventListener('submit',()=>sessionStorage.setItem('okww-email-report',String(input.checked)),{capture:true});"
        "}"
        "}"
        "const okwwObserver=new MutationObserver(records=>{"
        "for(const record of records){"
        "if(record.type==='attributes'){okwwScheduleTranslation(document.body);continue;}"
        "for(const addedNode of record.addedNodes){"
        "if(addedNode.nodeType===Node.TEXT_NODE){"
        "okwwScheduleTranslation(addedNode.parentNode);"
        "}else if(addedNode.nodeType===Node.ELEMENT_NODE){"
        "okwwScheduleTranslation(addedNode);okwwEnhanceScheduleForms(addedNode);"
        "}"
        "}"
        "}"
        "});"
        "okwwObserver.observe(document.documentElement,{subtree:true,childList:true,"
        "attributes:true,attributeFilter:['lang']});"
        "document.addEventListener('DOMContentLoaded',()=>{okwwScheduleTranslation(document.body);okwwEnhanceScheduleForms(document);});"
        "const okwwOriginalFetch=window.fetch.bind(window);window.fetch=async(input,init)=>{"
        "const url=typeof input==='string'?input:input.url;"
        "if(init?.method==='POST'&&url.startsWith('/api/schedule')&&init.body){"
        "try{const body=JSON.parse(init.body);body.email_report=sessionStorage.getItem('okww-email-report')!=='false';"
        "init={...init,body:JSON.stringify(body)};}catch{}}"
        "return okwwOriginalFetch(input,init);};"
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
        "async function okwwRefreshDockerUpdate(){"
        "const button=document.getElementById('okww-docker-update');"
        "const version=document.getElementById('okww-docker-version');"
        "if(!button||!version)return;"
        "try{const response=await fetch('/api/docker-update');const state=await response.json();"
        "const busy=['queued','checking','deferred','updating','rebuilding'].includes(state.status);"
        "button.disabled=!state.enabled||busy||state.task_running||state.game_running;"
        "button.textContent=busy?'Updating Docker...':'Update Docker';"
        "button.title=state.message||state.status||'';"
        "version.textContent=state.current_version||'dev';okwwTranslateRoot(button);"
        "}catch(error){button.disabled=true;button.title=String(error);}}"
        "async function okwwRequestDockerUpdate(){"
        "const button=document.getElementById('okww-docker-update');button.disabled=true;"
        "try{const response=await fetch('/api/docker-update',{method:'POST',"
        "headers:{'X-OK-WW-Update':'1'}});"
        "if(!response.ok)throw new Error(await response.text());}"
        "catch(error){button.title=String(error);}finally{await okwwRefreshDockerUpdate();}"
        "}"
        "document.addEventListener('DOMContentLoaded',()=>{"
        "const controls=document.createElement('div');controls.id='okww-cloud-controls';"
        "controls.style.cssText='position:fixed;right:20px;bottom:20px;z-index:2147483647;"
        "display:flex;gap:8px;align-items:center';"
        "const button=document.createElement('button');button.id='okww-cloud-game-control';"
        "button.type='button';button.textContent='Start Cloud Game';"
        "button.style.cssText='padding:8px 14px;border:0;border-radius:8px;background:#2563eb;color:white;"
        "font:600 14px sans-serif;cursor:pointer;box-shadow:0 2px 8px #0004';"
        "button.addEventListener('click',okwwToggleCloudGame);controls.appendChild(button);"
        "const update=document.createElement('button');update.id='okww-docker-update';"
        "update.type='button';update.textContent='Update Docker';"
        "update.style.cssText='position:relative;padding:8px 14px;border:0;border-radius:8px;"
        "background:#374151;color:white;font:600 14px sans-serif;cursor:pointer;"
        "box-shadow:0 2px 8px #0004';"
        "update.addEventListener('click',okwwRequestDockerUpdate);controls.appendChild(update);"
        "const version=document.createElement('span');version.id='okww-docker-version';"
        "version.style.cssText='position:absolute;right:0;top:calc(100% + 3px);opacity:.55;"
        "font:11px/1.2 monospace;color:currentColor;pointer-events:none;white-space:nowrap';"
        "version.textContent='dev';controls.appendChild(version);document.body.appendChild(controls);"
        "okwwRefreshCloudGame();okwwRefreshDockerUpdate();"
        "setInterval(okwwRefreshCloudGame,2000);setInterval(okwwRefreshDockerUpdate,2000);"
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
        task_state_signal: Any = None,
        update_control: DockerUpdateControl | None = None,
    ) -> None:
        self.runtime = runtime
        self.session = session
        self.profile = profile
        self.failure_notifier = failure_notifier
        self._lock = threading.RLock()
        self._active_task: Any = None
        self._active_task_started = False
        self._manual_game_thread: threading.Thread | None = None
        self._manual_game_error: str | None = None
        self._manual_game_stop_requested = False
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._scheduled_request: ScheduledTaskRequest | None = None
        self.schedule_manager: Any = None
        self._original_start = runtime.start
        self._original_start_task = runtime.start_task
        self._original_task_action = runtime.task_action
        self._original_stop_task = runtime.stop_task
        self._original_close = runtime.close
        self._task_done_signal = task_done_signal
        self._task_state_signal = task_state_signal
        self._update_control = update_control
        if task_done_signal is not None:
            task_done_signal.connect(self._on_task_done)
        if task_state_signal is not None:
            task_state_signal.connect(self._on_task_state_changed)

    def install(self) -> None:
        self.runtime.start = self.start
        self.runtime.start_task = self.start_task
        self.runtime.task_action = self.task_action
        self.runtime.stop_task = self.stop_task
        self.runtime.close = self.close

    def start(self):
        result = self._original_start()
        if self._monitor_thread is None:
            self._monitor_stop.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_browser, name="cloud-browser-monitor", daemon=True
            )
            self._monitor_thread.start()
        if self.schedule_manager is not None:
            self.schedule_manager.start()
        return result

    def start_task(self, task_identifier: str, *, allow_manual_login: bool | None = None):
        with self._lock:
            if self._update_control is not None and self._update_control.update_in_progress():
                raise RuntimeError("A Docker update is in progress")
            if self._active_task is not None:
                raise RuntimeError("Another task is already running")
            if self._manual_game_thread is not None and self._manual_game_thread.is_alive():
                raise RuntimeError("The cloud game is still starting or stopping")
            task, _is_trigger = self.runtime.ok.get_task(task_identifier)
            self._active_task = task
            self._active_task_started = False
            if self._update_control is not None:
                self._update_control.set_runtime_busy(True)
            if allow_manual_login is None:
                allow_manual_login = not self.profile.is_enrolled(
                    self.session.settings.cloud_url
                )
            try:
                # Background triggers must not capture login/queue pages. The
                # upstream task starter resumes the executor once in game.
                self.runtime.pause()
                self.session.start_game(allow_manual_login=allow_manual_login)
                return self._original_start_task(task_identifier)
            except AuthenticationRequiredError as exc:
                self._notify_login_failure(exc)
                self._active_task = None
                self._active_task_started = False
                if self._update_control is not None:
                    self._update_control.set_runtime_busy(False)
                self._stop_game_session()
                raise
            except BaseException:
                self._active_task = None
                self._active_task_started = False
                if self._update_control is not None:
                    self._update_control.set_runtime_busy(False)
                self._stop_game_session()
                raise

    def enqueue_scheduled(self, request: ScheduledTaskRequest) -> object:
        with self._lock:
            if self._active_task is not None:
                raise RuntimeError("Another task is already running")
            self._scheduled_request = request
            try:
                return self.start_task(request.task_id, allow_manual_login=False)
            except BaseException:
                self._scheduled_request = None
                raise

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
            if self._update_control is not None and self._update_control.update_in_progress():
                raise RuntimeError("A Docker update is in progress")
            if self._active_task is not None:
                raise RuntimeError("An automation task is already running")
            if self._manual_game_thread is not None and self._manual_game_thread.is_alive():
                return self.manual_game_status()
            self._manual_game_error = None
            self._manual_game_stop_requested = False
            if self._update_control is not None:
                self._update_control.set_runtime_busy(True)
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
                    try:
                        self._stop_game_session()
                    except Exception:
                        logger.exception("failed to clean up cloud-game start")
            if self._update_control is not None:
                self._update_control.set_runtime_busy(False)

    def stop_game_manually(self) -> dict[str, object]:
        with self._lock:
            self._manual_game_stop_requested = True
            task_running = self._active_task is not None

        if task_running:
            self.stop_task()
        else:
            self._stop_game_session()
            if self._update_control is not None:
                self._update_control.set_runtime_busy(False)

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

    def _stop_game_session(self) -> None:
        # Keep trigger preferences intact; pause before disconnecting capture.
        # Also publish executor_paused so the Web toolbar reflects idle state.
        try:
            self.runtime.pause()
        finally:
            self.session.stop()

    def _monitor_browser(self) -> None:
        while not self._monitor_stop.wait(1.0):
            try:
                self._check_browser_closed()
            except Exception:
                logger.exception("failed to check cloud browser state")

    def _check_browser_closed(self) -> None:
        with self._lock:
            if self._manual_game_thread is not None and self._manual_game_thread.is_alive():
                return
            if not self.session.browser_closed():
                return
            logger.info("cloud browser was closed; stopping the session")
            if self._active_task is not None:
                # A queued task might not yet be executor.current_task.
                self._original_task_action(self._active_task.name, "stop")
                self._close_active_game()
            else:
                self._stop_game_session()
                if self._update_control is not None:
                    self._update_control.set_runtime_busy(False)

    def _close_active_game(
        self, task_identifier: str | None = None, *, succeeded: bool = False
    ) -> None:
        with self._lock:
            if self._active_task is None:
                return
            task = self._active_task
            if task_identifier is not None:
                requested_task, _is_trigger = self.runtime.ok.get_task(task_identifier)
                if requested_task is not task:
                    return
            self._active_task = None
            self._active_task_started = False
            scheduled_request = self._scheduled_request
            self._scheduled_request = None
            # Serialize teardown with start_task so completion cannot pause or
            # close a session already opened by the next scheduled task.
            try:
                self._stop_game_session()
            except Exception:
                logger.exception("failed to close the cloud-game tab")
            finally:
                if self._update_control is not None:
                    self._update_control.set_runtime_busy(False)
        if scheduled_request is not None and scheduled_request.email_report:
            self._send_scheduled_report(task, scheduled_request, succeeded=succeeded)

    def _send_scheduled_report(
        self, task: Any, request: ScheduledTaskRequest, *, succeeded: bool
    ) -> None:
        notifier = self.failure_notifier
        if notifier is None or not hasattr(notifier, "send_task_report"):
            return
        details = "" if succeeded else "任务异常结束或被停止"

        def send() -> None:
            try:
                notifier.send_task_report(
                    schedule_name=request.schedule_name or request.schedule_id,
                    task_name=str(getattr(task, "name", request.task_id)),
                    succeeded=succeeded,
                    details=details,
                )
            except Exception:
                logger.exception("failed to send scheduled task report")

        try:
            threading.Thread(
                target=send, name="scheduled-task-email", daemon=True
            ).start()
        except Exception:
            logger.exception("failed to start scheduled task report sender")

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
        self._close_active_game(succeeded=True)

    def _on_task_state_changed(self, task: Any) -> None:
        with self._lock:
            active_task = self._active_task
            if active_task is None:
                return
            if task is active_task and bool(getattr(active_task, "running", False)):
                self._active_task_started = True
                return
            should_close = self._active_task_started and not bool(
                getattr(active_task, "running", False)
            )
        if should_close:
            self._close_active_game()

    def close(self) -> None:
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None
        if self._task_done_signal is not None:
            try:
                self._task_done_signal.disconnect(self._on_task_done)
            except Exception:
                pass
        if self._task_state_signal is not None:
            try:
                self._task_state_signal.disconnect(self._on_task_state_changed)
            except Exception:
                pass
        try:
            if self.schedule_manager is not None:
                self.schedule_manager.stop()
            self._stop_game_session()
        finally:
            if self._update_control is not None:
                self._update_control.set_runtime_busy(False)
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


def _cloud_update_status(config: dict[str, Any]) -> dict[str, object]:
    """Return a stable no-update response for container deployments."""

    return {
        "current_version": str(config.get("version") or "dev"),
        "versions": [],
        "update_available": False,
    }


def _running_version(config: dict[str, Any]) -> str:
    configured = os.environ.get("OK_WW_BUILD_VERSION", "").strip()
    if configured and configured != "dev":
        return configured
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        packaged = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        packaged = ""
    return packaged or configured or str(config.get("version") or "dev")


def _disable_cloud_update_checks(runtime: Any, config: dict[str, Any]) -> None:
    original_about = runtime.about

    def cloud_about() -> dict[str, Any]:
        return {**original_about(), "update_supported": False}

    runtime.about = cloud_about
    runtime.check_for_updates = lambda release_only=True: _cloud_update_status(config)


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
        from fastapi import Header, HTTPException
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
    _install_schedule_email_support(runtime)
    _install_slash_safe_task_routes(app, runtime)
    _disable_cloud_update_checks(runtime, ok_instance.config)
    update_control = DockerUpdateControl(
        settings.data_dir,
        fallback_version=_running_version(ok_instance.config),
    )
    update_control.set_runtime_busy(False)

    smtp_settings = SmtpSettings.from_env()
    controller = CloudWebController(
        runtime,
        session,
        profile,
        failure_notifier=(
            SmtpFailureNotifier(smtp_settings) if smtp_settings is not None else None
        ),
        task_done_signal=communicate.task_done,
        task_state_signal=communicate.task,
        update_control=update_control,
    )
    controller.install()
    app.state.cloud_controller = controller

    if schedule_manager_factory is None:
        from .cloud_schedule_manager import CloudScheduleManager

        schedule_manager = CloudScheduleManager(
            settings.data_dir / "schedules.json", controller.enqueue_scheduled,
            onetime_tasks=runtime.executor.onetime_tasks,
        )
    else:
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

    @app.get("/api/docker-update", include_in_schema=False)
    async def docker_update_status():
        game_status = controller.manual_game_status()
        return {
            **update_control.status(),
            "task_running": game_status["task_running"],
            "game_running": game_status["running"],
        }

    @app.post("/api/docker-update", include_in_schema=False, status_code=202)
    async def request_docker_update(
        x_ok_ww_update: str | None = Header(default=None),
    ):
        if x_ok_ww_update != "1":
            raise HTTPException(status_code=403, detail="Missing update confirmation")
        game_status = controller.manual_game_status()
        if game_status["task_running"]:
            raise HTTPException(status_code=409, detail="An automation task is running")
        if game_status["running"]:
            raise HTTPException(status_code=409, detail="The cloud game is running")
        try:
            return update_control.request_update()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

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
