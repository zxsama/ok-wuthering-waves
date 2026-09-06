"""Playwright adapter for the public Wuthering Waves cloud-game page.

The site-specific knowledge in this module is deliberately limited to public
labels, generic media elements, and page-level runtime flags.  Login credentials
are never read or written here; Chromium's persistent user-data directory owns
the authenticated browser session.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread, current_thread
from typing import Any

from .models import CloudPageError, CloudPageState, QueueKind


@dataclass(frozen=True, slots=True)
class CloudPageSelectors:
    """Maintainable public-facing selectors used by the cloud page adapter."""

    login_actions: tuple[str, ...] = ("立即登录",)
    login_frame_css: str = (
        "iframe.kr-sdk-iframe:visible, "
        "iframe[src*='usercenter.kurogames.com']:visible"
    )
    landing_actions: tuple[str, ...] = ("开始游戏",)
    request_game_actions: tuple[str, ...] = ("进入游戏", "开始游戏")
    authenticated_actions: tuple[str, ...] = ("退出登录",)
    dismiss_overlay_texts: tuple[str, ...] = ("点击空白区域关闭",)
    queue_prompt_texts: tuple[str, ...] = ("请选择排队队列",)
    normal_queue_actions: tuple[str, ...] = ("普通队列",)
    fast_queue_actions: tuple[str, ...] = ("快速队列",)
    queueing_texts: tuple[str, ...] = ("排队中", "预计等待时间", "退出排队")
    queue_ready_texts: tuple[str, ...] = ("排队成功",)
    queue_ready_actions: tuple[str, ...] = ("进入游戏（", "进入游戏(")
    launching_texts: tuple[str, ...] = (
        "正在启动",
        "启动游戏中",
        "游戏启动中",
        "加载游戏中",
    )
    error_texts: tuple[str, ...] = (
        "网络异常",
        "加载失败",
        "连接断开",
        "游戏已断开",
        "云服务错误",
        "云服务异常",
        "云游戏启动超时",
        "云游戏启动失败",
        "云游戏连接失败",
    )
    media_surface_css: str = "video:visible, canvas:visible"


class PlaywrightCloudPageAdapter:
    """Drive a persistent system Chrome/Edge session through Playwright."""

    _RUNTIME_STATE_SCRIPT = """() => ({
        gameRunning: Boolean(window.GameRunning),
        gameBootState: Number(
            window.GameBootState ?? window.gameBootState ?? -1
        )
    })"""

    def __init__(
        self,
        *,
        channels: Sequence[str] = ("chrome", "msedge"),
        selectors: CloudPageSelectors | None = None,
        action_timeout_seconds: float = 15.0,
        navigation_timeout_seconds: float = 60.0,
        owner_open_timeout_seconds: float = 90.0,
        owner_call_timeout_seconds: float = 30.0,
        owner_close_timeout_seconds: float = 15.0,
        viewport: tuple[int, int] = (1280, 720),
        launch_args: Sequence[str] = ("--deny-permission-prompts",),
        ignore_default_args: Sequence[str] = ("--no-sandbox",),
        playwright_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not channels:
            raise ValueError("at least one system browser channel is required")
        if action_timeout_seconds <= 0 or navigation_timeout_seconds <= 0:
            raise ValueError("browser timeouts must be positive")
        if min(
            owner_open_timeout_seconds,
            owner_call_timeout_seconds,
            owner_close_timeout_seconds,
        ) <= 0:
            raise ValueError("owner dispatcher timeouts must be positive")

        self.channels = tuple(channels)
        self.selectors = selectors or CloudPageSelectors()
        self.action_timeout_seconds = action_timeout_seconds
        self.navigation_timeout_seconds = navigation_timeout_seconds
        self.owner_open_timeout_seconds = owner_open_timeout_seconds
        self.owner_call_timeout_seconds = owner_call_timeout_seconds
        self.owner_close_timeout_seconds = owner_close_timeout_seconds
        self.viewport = viewport
        self.launch_args = tuple(launch_args)
        self.ignore_default_args = tuple(ignore_default_args)
        self._playwright_factory = playwright_factory
        self._clock = clock
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._owner_thread: Thread | None = None
        self._owner_queue: Queue[tuple[Callable[[], Any] | None, Future[Any]]] | None = None
        self._owner_lock = Lock()
        self._closing = False
        self._close_future: Future[Any] | None = None
        self.active_channel: str | None = None

    def open(self, *, url: str, profile_dir: Path, visible: bool) -> None:
        profile_dir.mkdir(parents=True, exist_ok=True)
        ready: Future[None] = Future()
        with self._owner_lock:
            if self._context is not None or (
                self._owner_thread is not None and self._owner_thread.is_alive()
            ):
                raise CloudPageError("cloud browser is already open")
            self._closing = False
            self._close_future = None
            self._owner_queue = Queue()
            owner = Thread(
                target=self._owner_main,
                args=(url, profile_dir, visible, ready),
                name="cloud-browser-owner",
                daemon=True,
            )
            self._owner_thread = owner
        owner.start()
        try:
            ready.result(timeout=self.owner_open_timeout_seconds)
        except FutureTimeoutError as exc:
            if ready.done():
                ready.result()
            self._begin_close()
            raise CloudPageError(
                "timed out waiting for cloud browser owner to open"
            ) from exc
        except BaseException:
            owner.join(timeout=self.owner_close_timeout_seconds)
            raise

    def _owner_main(
        self,
        url: str,
        profile_dir: Path,
        visible: bool,
        ready: Future[None],
    ) -> None:
        queue = self._owner_queue
        assert queue is not None
        terminal_error: BaseException = CloudPageError(
            "cloud browser owner thread stopped"
        )
        close_result: Future[Any] | None = None
        try:
            try:
                self._open_resources(url=url, profile_dir=profile_dir, visible=visible)
            except BaseException as exc:
                terminal_error = exc
                self._set_future_exception(ready, exc)
                return

            with self._owner_lock:
                closing = self._closing
            if closing:
                terminal_error = CloudPageError(
                    "cloud browser owner was closed while opening"
                )
                self._set_future_exception(ready, terminal_error)
                return

            self._set_future_result(ready, None)
            while True:
                callback, result = queue.get()
                if callback is None:
                    close_result = result
                    break
                try:
                    self._set_future_result(result, callback())
                except BaseException as exc:
                    self._set_future_exception(result, exc)
        except BaseException as exc:
            terminal_error = exc
            self._set_future_exception(ready, exc)
        finally:
            with self._owner_lock:
                self._closing = True
            try:
                self._close_resources()
            except BaseException as exc:
                terminal_error = exc
                if close_result is not None:
                    self._set_future_exception(close_result, exc)
            else:
                if close_result is not None:
                    self._set_future_result(close_result, None)
            self._fail_pending_owner_calls(queue, terminal_error)
            with self._owner_lock:
                if self._owner_thread is current_thread():
                    self._owner_thread = None
                    self._owner_queue = None
                    self._close_future = None
                    self._closing = False

    def _open_resources(self, *, url: str, profile_dir: Path, visible: bool) -> None:
        playwright = self._start_playwright()
        launch_errors: list[str] = []
        context = None

        for channel in self.channels:
            try:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel=channel,
                    headless=not visible,
                    args=list(self.launch_args),
                    ignore_default_args=list(self.ignore_default_args),
                    viewport={"width": self.viewport[0], "height": self.viewport[1]},
                    accept_downloads=False,
                )
                self.active_channel = channel
                break
            except Exception as exc:  # Playwright errors vary by installed version.
                launch_errors.append(f"{channel}: {exc}")

        if context is None:
            self._stop_playwright(playwright)
            detail = "; ".join(launch_errors)
            raise CloudPageError(
                f"cannot launch a system Chrome/Edge channel ({detail})"
            )

        self._playwright = playwright
        self._context = context
        page = context.pages[0] if context.pages else context.new_page()
        self._page = page
        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._milliseconds(self.navigation_timeout_seconds),
            )
        except Exception as exc:
            self._close_resources()
            raise CloudPageError(f"cannot open cloud-game page: {exc}") from exc

    def observe(self) -> CloudPageState:
        return self._call_owner(self._observe_on_owner)

    def _observe_on_owner(self) -> CloudPageState:
        page = self._page
        if page is None or self._is_page_closed(page):
            return CloudPageState.CLOSED

        if self._any_visible_text(self.selectors.error_texts):
            return CloudPageState.ERROR
        if self._login_required_visible():
            return CloudPageState.LOGIN_REQUIRED
        if self._any_visible_text(self.selectors.queue_ready_texts) or self._any_visible_action(
            self.selectors.queue_ready_actions, exact=False
        ):
            return CloudPageState.READY_TO_ENTER
        if self._any_visible_text(self.selectors.queueing_texts):
            return CloudPageState.QUEUEING
        if self._any_visible_text(self.selectors.queue_prompt_texts) or self._any_visible_action(
            self.selectors.normal_queue_actions + self.selectors.fast_queue_actions
        ):
            return CloudPageState.QUEUE_SELECTION

        runtime = self._runtime_state()
        has_media = self._visible_css(self.selectors.media_surface_css)
        if self._any_visible_text(self.selectors.launching_texts):
            return CloudPageState.LAUNCHING
        if runtime["game_running"] and has_media:
            return CloudPageState.IN_GAME
        if runtime["game_running"] or runtime["game_boot_state"] in {1, 2, 3}:
            return CloudPageState.LAUNCHING
        request_game_visible = self._any_visible_action(self.selectors.request_game_actions)
        if request_game_visible and self._has_authentication_evidence():
            return CloudPageState.HOME
        if request_game_visible:
            return CloudPageState.LANDING
        return CloudPageState.UNKNOWN

    def request_game(self) -> None:
        self._call_owner(self._request_game_on_owner)

    def _request_game_on_owner(self) -> None:
        self._dismiss_known_overlays()
        try:
            self._click_action("request game", self.selectors.request_game_actions)
        except CloudPageError:
            # The cross-origin login frame can appear between observation and
            # the click and legitimately intercept it.  That is the expected
            # next state, not an action failure.
            if self._login_required_visible():
                return
            raise

    def _dismiss_known_overlays(self) -> None:
        if not self._any_visible_text(self.selectors.dismiss_overlay_texts):
            return
        page = self._require_page()
        try:
            page.mouse.click(8, 8)
            page.wait_for_timeout(300)
        except Exception as exc:
            raise CloudPageError(f"failed to dismiss cloud page overlay: {exc}") from exc

    def select_queue(self, queue: QueueKind) -> None:
        self._call_owner(lambda: self._select_queue_on_owner(queue))

    def _select_queue_on_owner(self, queue: QueueKind) -> None:
        labels = (
            self.selectors.normal_queue_actions
            if queue == QueueKind.NORMAL
            else self.selectors.fast_queue_actions
        )
        self._click_action(f"select {queue.value} queue", labels)

    def enter_game(self) -> None:
        self._call_owner(self._enter_game_on_owner)

    def _enter_game_on_owner(self) -> None:
        self._click_action(
            "enter queued game",
            self.selectors.queue_ready_actions,
            exact=False,
        )

    def close(self) -> None:
        with self._owner_lock:
            owner = self._owner_thread
        if owner is not None and owner.is_alive():
            result = self._begin_close()
            if current_thread() is owner:
                return
            if result is None:
                owner.join(timeout=self.owner_close_timeout_seconds)
                if owner.is_alive():
                    raise CloudPageError(
                        "timed out waiting for cloud browser owner thread to stop"
                    )
                return
            try:
                result.result(timeout=self.owner_close_timeout_seconds)
            except FutureTimeoutError as exc:
                if result.done():
                    result.result()
                raise CloudPageError(
                    "timed out waiting for cloud browser owner to close"
                ) from exc
            owner.join(timeout=self.owner_close_timeout_seconds)
            if owner.is_alive():
                raise CloudPageError(
                    "timed out waiting for cloud browser owner thread to stop"
                )
            return
        with self._owner_lock:
            self._owner_thread = None
            self._owner_queue = None
            self._close_future = None
            self._closing = False
        self._close_resources()

    def call_page(self, callback: Callable[[Any], Any]) -> Any:
        """Run ``callback`` synchronously on the Playwright owner thread."""

        return self._call_owner(lambda: callback(self._require_page()))

    def _call_owner(self, callback: Callable[[], Any]) -> Any:
        with self._owner_lock:
            owner = self._owner_thread
            queue = self._owner_queue
            closing = self._closing
        if owner is None or current_thread() is owner:
            if closing:
                raise CloudPageError("cloud browser owner thread is closing")
            return callback()
        if closing:
            raise CloudPageError("cloud browser owner thread is closing")
        if not owner.is_alive():
            raise CloudPageError("cloud browser owner thread is unavailable")
        if queue is None:
            raise CloudPageError("cloud browser owner thread is unavailable")
        result: Future[Any] = Future()
        queue.put((callback, result))
        if not owner.is_alive():
            self._set_future_exception(
                result,
                CloudPageError("cloud browser owner thread is unavailable"),
            )
        try:
            return result.result(timeout=self.owner_call_timeout_seconds)
        except FutureTimeoutError as exc:
            if result.done():
                return result.result()
            raise CloudPageError(
                "timed out waiting for cloud browser owner call"
            ) from exc

    def _begin_close(self) -> Future[Any] | None:
        with self._owner_lock:
            owner = self._owner_thread
            queue = self._owner_queue
            if owner is None or not owner.is_alive() or queue is None:
                return None
            self._closing = True
            if self._close_future is None:
                self._close_future = Future()
                queue.put((None, self._close_future))
            return self._close_future

    @staticmethod
    def _set_future_result(future: Future[Any], value: Any) -> None:
        if not future.done():
            future.set_result(value)

    @staticmethod
    def _set_future_exception(future: Future[Any], exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _fail_pending_owner_calls(
        self,
        queue: Queue[tuple[Callable[[], Any] | None, Future[Any]]],
        exc: BaseException,
    ) -> None:
        while True:
            try:
                _, result = queue.get_nowait()
            except Empty:
                return
            self._set_future_exception(result, exc)

    def _close_resources(self) -> None:
        context, playwright = self._context, self._playwright
        self._page = None
        self._context = None
        self._playwright = None
        self.active_channel = None
        try:
            if context is not None:
                context.close()
        finally:
            if playwright is not None:
                self._stop_playwright(playwright)

    def _start_playwright(self) -> Any:
        try:
            factory = self._playwright_factory
            if factory is None:
                factory = import_module("playwright.sync_api").sync_playwright
            return factory().start()
        except Exception as exc:
            raise CloudPageError(f"cannot initialize Playwright: {exc}") from exc

    @staticmethod
    def _stop_playwright(playwright: Any) -> None:
        try:
            playwright.stop()
        except Exception:
            pass

    @staticmethod
    def _is_page_closed(page: Any) -> bool:
        try:
            return bool(page.is_closed())
        except Exception:
            return True

    def _runtime_state(self) -> dict[str, Any]:
        try:
            value = self._require_page().evaluate(self._RUNTIME_STATE_SCRIPT)
        except Exception:
            return {"game_running": False, "game_boot_state": -1}
        if not isinstance(value, dict):
            return {"game_running": False, "game_boot_state": -1}
        return {
            "game_running": bool(value.get("gameRunning", False)),
            "game_boot_state": self._safe_int(value.get("gameBootState"), -1),
        }

    def _has_authentication_evidence(self) -> bool:
        if self._any_visible_action(self.selectors.authenticated_actions):
            return True
        try:
            # Return only an existence bit.  Authentication payloads and token
            # values stay inside the persistent browser profile.
            return bool(
                self._require_page().evaluate(
                    """() => {
                        try {
                            const raw = window.localStorage?.getItem(
                                'useMcCloudGameAppStore'
                            );
                            if (!raw) return false;
                            const store = JSON.parse(raw);
                            const login = store?.sdkLoginInfo ??
                                store?.state?.sdkLoginInfo;
                            return Boolean(
                                login && (
                                    login.token || login.cuid || login.uid ||
                                    login.userId
                                )
                            );
                        } catch (_) {
                            return false;
                        }
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _any_visible_text(self, labels: Iterable[str]) -> bool:
        page = self._require_page()
        for label in labels:
            try:
                if page.get_by_text(label, exact=False).first.is_visible(timeout=0):
                    return True
            except Exception:
                continue
        return False

    def _login_required_visible(self) -> bool:
        return self._visible_css(self.selectors.login_frame_css) or self._any_visible_action(
            self.selectors.login_actions
        )

    def _any_visible_action(self, labels: Iterable[str], *, exact: bool = True) -> bool:
        return self._find_visible_action(labels, exact=exact) is not None

    def _find_visible_action(self, labels: Iterable[str], *, exact: bool) -> Any | None:
        page = self._require_page()
        for label in labels:
            candidates = (
                page.get_by_role("button", name=label, exact=exact),
                page.get_by_text(label, exact=exact),
            )
            for candidate in candidates:
                try:
                    if candidate.first.is_visible(timeout=0):
                        return candidate.first
                except Exception:
                    continue
        return None

    def _visible_css(self, selector: str) -> bool:
        try:
            return self._require_page().locator(selector).first.is_visible(timeout=0)
        except Exception:
            return False

    def _click_action(
        self,
        action: str,
        labels: Iterable[str],
        *,
        exact: bool = True,
    ) -> None:
        label_tuple = tuple(labels)
        deadline = self._clock() + self.action_timeout_seconds
        last_error: Exception | None = None
        while self._clock() < deadline:
            locator = self._find_visible_action(label_tuple, exact=exact)
            if locator is not None:
                try:
                    remaining = max(deadline - self._clock(), 0.1)
                    locator.click(timeout=self._milliseconds(min(remaining, 1.0)))
                    return
                except Exception as exc:
                    last_error = exc
                    if self._any_visible_text(self.selectors.dismiss_overlay_texts):
                        try:
                            self._require_page().mouse.click(8, 8)
                            self._require_page().wait_for_timeout(300)
                        except Exception:
                            pass
            self._require_page().wait_for_timeout(100)
        expected = ", ".join(label_tuple)
        if last_error is not None:
            raise CloudPageError(f"failed to {action}: {last_error}") from last_error
        raise CloudPageError(
            f"timed out trying to {action}; expected a visible action: {expected}"
        )

    def _require_page(self) -> Any:
        if self._page is None or self._is_page_closed(self._page):
            raise CloudPageError("cloud browser page is not open")
        return self._page

    @staticmethod
    def _milliseconds(seconds: float) -> float:
        return seconds * 1000


def create_playwright_adapter() -> PlaywrightCloudPageAdapter:
    """CLI-compatible zero-argument adapter factory."""

    return PlaywrightCloudPageAdapter()
