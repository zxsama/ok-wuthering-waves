from __future__ import annotations

import time
from concurrent.futures import Future
from pathlib import Path
from queue import Queue
from threading import Event, Thread, get_ident

import pytest

from extensions.cloud.models import CloudPageError, CloudPageState, QueueKind
from extensions.cloud.playwright_adapter import PlaywrightCloudPageAdapter


class FakeLocator:
    def __init__(self, page, key, visible=False):
        self.page = page
        self.key = key
        self.visible = visible

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return self.visible

    def click(self, timeout=0):
        if self.page.click_error is not None:
            raise self.page.click_error
        self.page.clicks.append(self.key)


class FakePage:
    def __init__(
        self, *, actions=(), texts=(), css=(), runtime=None, has_login_info=False
    ):
        self.actions = set(actions)
        self.texts = set(texts)
        self.css = set(css)
        self.runtime = runtime or {"gameRunning": False, "gameBootState": -1}
        self.has_login_info = has_login_info
        self.clicks = []
        self.goto_args = None
        self.call_threads = []
        self.closed = False
        self.click_error = None
        self.mouse = FakeMouse(self)

    def is_closed(self):
        self.call_threads.append(get_ident())
        return self.closed

    def get_by_role(self, role, *, name, exact):
        return FakeLocator(self, ("role", role, name), name in self.actions)

    def get_by_text(self, text, *, exact):
        visible = text in self.actions or text in self.texts
        if not exact:
            visible = visible or any(text in value for value in self.actions | self.texts)
        return FakeLocator(self, ("text", text), visible)

    def locator(self, selector):
        return FakeLocator(self, ("css", selector), selector in self.css)

    def evaluate(self, script):
        self.call_threads.append(get_ident())
        if "useMcCloudGameAppStore" in script:
            assert "store?.sdkLoginInfo" in script
            assert "store?.state?.sdkLoginInfo" in script
            return self.has_login_info
        return self.runtime

    def wait_for_timeout(self, timeout):
        return None

    def goto(self, url, **kwargs):
        self.call_threads.append(get_ident())
        self.goto_args = (url, kwargs)


class FakeMouse:
    def __init__(self, page):
        self.page = page

    def click(self, x, y):
        self.page.clicks.append(("mouse", x, y))
        self.page.texts.discard("点击空白区域关闭")
        self.page.click_error = None


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.closed = False
        self.close_thread = None

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.close_thread = get_ident()
        self.closed = True


class FakeChromium:
    def __init__(self, page, failing_channels=()):
        self.page = page
        self.failing_channels = set(failing_channels)
        self.launches = []
        self.context = None
        self.launch_threads = []

    def launch_persistent_context(self, **kwargs):
        self.launch_threads.append(get_ident())
        self.launches.append(kwargs)
        if kwargs["channel"] in self.failing_channels:
            raise RuntimeError("channel unavailable")
        self.context = FakeContext(self.page)
        return self.context


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False
        self.stop_thread = None

    def stop(self):
        self.stop_thread = get_ident()
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright
        self.start_thread = None

    def start(self):
        self.start_thread = get_ident()
        self.playwright.start_thread = self.start_thread
        return self.playwright


def make_open_adapter(page, *, failing_channels=()):
    chromium = FakeChromium(page, failing_channels)
    playwright = FakePlaywright(chromium)
    adapter = PlaywrightCloudPageAdapter(
        playwright_factory=lambda: FakePlaywrightManager(playwright)
    )
    return adapter, chromium, playwright


def attach_page(page, *, timeout=0.001):
    adapter = PlaywrightCloudPageAdapter(action_timeout_seconds=timeout)
    adapter._page = page
    return adapter


def test_open_uses_persistent_profile_and_falls_back_to_edge(tmp_path):
    page = FakePage()
    adapter, chromium, playwright = make_open_adapter(
        page, failing_channels=("chrome",)
    )
    profile_dir = tmp_path / "profile"

    adapter.open(url="https://example.invalid/cloud/", profile_dir=profile_dir, visible=True)

    assert profile_dir.is_dir()
    assert [launch["channel"] for launch in chromium.launches] == ["chrome", "msedge"]
    assert chromium.launches[-1]["user_data_dir"] == str(profile_dir)
    assert chromium.launches[-1]["headless"] is False
    assert chromium.launches[-1]["args"] == ["--deny-permission-prompts"]
    assert chromium.launches[-1]["ignore_default_args"] == ["--no-sandbox"]
    assert adapter.active_channel == "msedge"
    assert page.goto_args[0] == "https://example.invalid/cloud/"

    context = chromium.context
    adapter.close()
    assert context.closed
    assert playwright.stopped


def test_open_page_calls_and_close_share_one_owner_thread(tmp_path):
    page = FakePage()
    adapter, chromium, playwright = make_open_adapter(page)
    caller_thread = get_ident()

    adapter.open(url="https://example.invalid/cloud/", profile_dir=tmp_path, visible=False)
    callback_thread = adapter.call_page(lambda current_page: get_ident())
    adapter.observe()
    context = chromium.context
    adapter.close()

    owner_thread = chromium.launch_threads[0]
    assert owner_thread != caller_thread
    assert playwright.start_thread == owner_thread
    assert callback_thread == owner_thread
    assert set(page.call_threads) == {owner_thread}
    assert context.close_thread == owner_thread
    assert playwright.stop_thread == owner_thread


def test_call_page_keeps_attach_page_compatible():
    page = FakePage()
    adapter = attach_page(page)

    assert adapter.call_page(lambda current_page: current_page) is page


def test_open_times_out_without_waiting_forever(tmp_path):
    release = Event()

    class BlockingManager:
        def start(self):
            release.wait()

    adapter = PlaywrightCloudPageAdapter(
        playwright_factory=BlockingManager,
        owner_open_timeout_seconds=0.01,
        owner_close_timeout_seconds=0.01,
    )

    started = time.monotonic()
    with pytest.raises(CloudPageError, match="timed out.*owner to open"):
        adapter.open(url="https://example.invalid", profile_dir=tmp_path, visible=False)
    assert time.monotonic() - started < 0.5

    release.set()
    owner = adapter._owner_thread
    if owner is not None:
        owner.join(timeout=1)


def test_call_page_times_out_without_waiting_forever(tmp_path):
    adapter, _, _ = make_open_adapter(FakePage())
    adapter.owner_call_timeout_seconds = 0.01
    adapter.open(url="https://example.invalid", profile_dir=tmp_path, visible=False)
    release = Event()

    started = time.monotonic()
    with pytest.raises(CloudPageError, match="timed out.*owner call"):
        adapter.call_page(lambda _: release.wait())
    assert time.monotonic() - started < 0.5

    release.set()
    adapter.close()


def test_close_times_out_and_rejects_new_calls_while_closing(tmp_path):
    adapter, _, _ = make_open_adapter(FakePage())
    adapter.owner_close_timeout_seconds = 0.01
    adapter.open(url="https://example.invalid", profile_dir=tmp_path, visible=False)
    release = Event()
    callback_started = Event()
    caller = Thread(
        target=lambda: adapter.call_page(
            lambda _: callback_started.set() or release.wait()
        )
    )
    caller.start()
    assert callback_started.wait(timeout=1)

    with pytest.raises(CloudPageError, match="timed out.*owner to close"):
        adapter.close()
    with pytest.raises(CloudPageError, match="owner thread is closing"):
        adapter.call_page(lambda page: page)

    release.set()
    caller.join(timeout=1)
    owner = adapter._owner_thread
    if owner is not None:
        owner.join(timeout=1)


def test_owner_shutdown_fails_all_pending_futures():
    adapter = PlaywrightCloudPageAdapter()
    pending_one = Future()
    pending_two = Future()
    queue = Queue()
    queue.put((lambda: None, pending_one))
    queue.put((None, pending_two))

    adapter._fail_pending_owner_calls(queue, CloudPageError("owner stopped"))

    with pytest.raises(CloudPageError, match="owner stopped"):
        pending_one.result()
    with pytest.raises(CloudPageError, match="owner stopped"):
        pending_two.result()


def test_owner_dispatcher_timeouts_must_be_positive():
    with pytest.raises(ValueError, match="owner dispatcher timeouts"):
        PlaywrightCloudPageAdapter(owner_call_timeout_seconds=0)


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (FakePage(actions={"立即登录"}), CloudPageState.LOGIN_REQUIRED),
        (
            FakePage(
                css={
                    "iframe.kr-sdk-iframe:visible, iframe[src*='usercenter.kurogames.com']:visible"
                }
            ),
            CloudPageState.LOGIN_REQUIRED,
        ),
        (FakePage(actions={"开始游戏"}), CloudPageState.LANDING),
        (
            FakePage(actions={"开始游戏"}, has_login_info=True),
            CloudPageState.HOME,
        ),
        (FakePage(actions={"进入游戏"}), CloudPageState.LANDING),
        (
            FakePage(actions={"进入游戏"}, has_login_info=True),
            CloudPageState.HOME,
        ),
        (FakePage(actions={"普通队列"}), CloudPageState.QUEUE_SELECTION),
        (FakePage(texts={"预计等待时间"}), CloudPageState.QUEUEING),
        (FakePage(texts={"排队成功"}), CloudPageState.READY_TO_ENTER),
        (
            FakePage(
                css={"video:visible, canvas:visible"},
                runtime={"gameRunning": True, "gameBootState": 1},
            ),
            CloudPageState.IN_GAME,
        ),
        (
            FakePage(runtime={"gameRunning": True, "gameBootState": 1}),
            CloudPageState.LAUNCHING,
        ),
        (
            FakePage(
                texts={"游戏启动中，请耐心等待~"},
                css={"video:visible, canvas:visible"},
                runtime={"gameRunning": True, "gameBootState": 1},
            ),
            CloudPageState.LAUNCHING,
        ),
        (FakePage(texts={"连接断开"}), CloudPageState.ERROR),
        (FakePage(texts={"云服务错误"}), CloudPageState.ERROR),
        (FakePage(texts={"云服务异常，请稍后重试"}), CloudPageState.ERROR),
        (FakePage(texts={"云游戏启动超时，请重试"}), CloudPageState.ERROR),
        (FakePage(texts={"云游戏启动失败"}), CloudPageState.ERROR),
        (FakePage(texts={"云游戏连接失败"}), CloudPageState.ERROR),
    ],
)
def test_observe_maps_public_signals_to_semantic_states(page, expected):
    assert attach_page(page).observe() == expected


def test_actions_click_only_expected_public_controls():
    page = FakePage(actions={"开始游戏", "进入游戏（5s）", "普通队列"})
    adapter = attach_page(page)

    adapter.request_game()
    adapter.select_queue(QueueKind.NORMAL)
    adapter.enter_game()

    assert page.clicks == [
        ("role", "button", "开始游戏"),
        ("role", "button", "普通队列"),
        ("text", "进入游戏（"),
    ]


def test_missing_action_raises_explicit_timeout():
    adapter = attach_page(FakePage(), timeout=0.0001)

    with pytest.raises(CloudPageError, match="timed out trying to request game"):
        adapter.request_game()


def test_request_game_accepts_login_frame_race():
    page = FakePage(
        actions={"开始游戏"},
        css={
            "iframe.kr-sdk-iframe:visible, iframe[src*='usercenter.kurogames.com']:visible"
        },
    )
    page.click_error = RuntimeError("login frame intercepted the click")

    attach_page(page).request_game()


def test_request_game_dismisses_known_overlay_before_clicking():
    page = FakePage(actions={"进入游戏"}, texts={"点击空白区域关闭"})

    attach_page(page).request_game()

    assert page.clicks == [
        ("mouse", 8, 8),
        ("role", "button", "进入游戏"),
    ]


def test_request_game_recovers_when_overlay_appears_during_click():
    page = FakePage(actions={"进入游戏"}, texts={"点击空白区域关闭"})
    page.click_error = RuntimeError("overlay intercepted click")

    attach_page(page).request_game()

    assert page.clicks[-1] == ("role", "button", "进入游戏")


def test_open_reports_all_unavailable_system_channels(tmp_path):
    page = FakePage()
    adapter, _, playwright = make_open_adapter(
        page, failing_channels=("chrome", "msedge")
    )

    with pytest.raises(CloudPageError, match="cannot launch a system Chrome/Edge"):
        adapter.open(url="https://example.invalid", profile_dir=Path(tmp_path), visible=False)

    assert playwright.stopped
