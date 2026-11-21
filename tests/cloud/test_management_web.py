from concurrent.futures import Future
from types import SimpleNamespace

import asyncio

import httpx
from ok.core.events import communicate

from extensions.cloud.management_web.app import create_web_app
from extensions.cloud.management_web.task_tabs import (
    WebCustomTab, WebTabConfig, task_tab_action, task_tab_query,
)
from extensions.cloud.ok_bridge import create_cloud_ok_class


class ExampleTab(WebCustomTab):
    @task_tab_query("state")
    def state(self):
        return {"value": self.value}

    @task_tab_action("save")
    def save(self, payload):
        self.value = payload["value"]
        self.emit_web_event("saved", self.state())
        return self.state()


def test_retained_management_ui_serves_tabs_and_current_core_events(tmp_path):
    (tmp_path / "index.js").write_text("export default {};", encoding="utf-8")
    tab = ExampleTab(executor=SimpleNamespace(scene=None), app=None)
    tab.name = "Example"
    tab.value = 1
    tab.web_tab = WebTabConfig(id="example", name="Example", asset_dir=tmp_path)
    instance = SimpleNamespace(task_executor=SimpleNamespace(get_all_tasks=lambda: [tab]))
    app = create_web_app({}, ok_instance=instance)
    messages = []
    with communicate.any.subscribed(messages.append):
        # No lifespan: this verifies HTTP behavior without starting task workers.
        async def request_tabs():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                assert (await client.get("/")).status_code == 200
                assert (await client.get("/static/app.js")).status_code == 200
                assert (await client.get("/api/task-tabs")).json()[0]["id"] == "example"
                assert (await client.get("/task-tabs/example/assets/index.js")).status_code == 200
                assert (await client.post("/api/task-tabs/example/query/state", json={})).json() == {"value": 1}
                assert (await client.post("/api/task-tabs/example/action/save", json={"value": 2})).json() == {"value": 2}
                assert (await client.post("/api/task-tabs/example/action/missing", json={})).status_code == 400

        asyncio.run(request_tabs())
    assert messages[-1].name == "task_tab"
    assert messages[-1].args == ("example", "saved", {"value": 2})


def test_cloud_ok_registers_local_tabs_once_without_mutating_source(monkeypatch):
    import ok

    captured = []

    class FakeOK:
        def __init__(self, config):
            captured.append(config)
            self.task_executor = SimpleNamespace(onetime_tasks=[])

    monkeypatch.setattr(ok, "OK", FakeOK)
    monkeypatch.setattr("extensions.cloud.daily_task.install_daily_task_support", lambda executor: None)
    monkeypatch.setattr("extensions.cloud.tacet_task.install_tacet_task_support", lambda executor: None)
    entry = [__name__, "ExampleTab"]
    config = {"web_runtime": True, "web_tabs": [entry, entry], "onetime_tasks": []}
    manager = SimpleNamespace(capture_method=None)
    create_cloud_ok_class(manager)(config)

    assert captured[0]["onetime_tasks"] == [entry]
    assert config["onetime_tasks"] == []

def test_section_reset_preserves_other_settings_and_includes_inactive_children():
    from extensions.cloud.management_web.app import WebRuntime

    option = SimpleNamespace(
        config_type={"Mode": {"group": "Capture", "sub_configs": {True: ["Quality"]}},
                     "Other setting": {"group": "Window"}},
        default_config={"Mode": True, "Quality": 3, "Other setting": 1},
    )
    config = {"Mode": False, "Quality": 8, "Other setting": 5}
    store = SimpleNamespace(get_config=lambda _: config, get_all_visible_configs=lambda: [("Options", config, option)])
    runtime = object.__new__(WebRuntime)
    runtime.ok = SimpleNamespace(task_executor=SimpleNamespace(global_config=store))
    runtime.reset_settings("Options", "Capture")
    assert config == {"Mode": True, "Quality": 3, "Other setting": 5}


def test_script_run_waits_for_reload_and_uses_task_start_entry_point(tmp_path):
    from extensions.cloud.management_web.app import WebRuntime

    class EditedTask:
        name = "Shared display name"

    path = tmp_path / "EditedTask.py"
    path.touch()
    task = EditedTask()
    reload_result = Future()
    manager = SimpleNamespace(
        task_folder=tmp_path, task_map={task: (path, "")}, task_errors={},
        reload_task_code=lambda _: SimpleNamespace(future=reload_result),
    )
    runtime = object.__new__(WebRuntime)
    runtime.ok = SimpleNamespace(task_manager=manager)
    starts = []
    runtime.start_task = starts.append

    reload_result.set_exception(SyntaxError("invalid task"))
    assert runtime.run_script(path.name, "invalid code")["error"] == "invalid task"
    assert starts == []

    reload_result = Future()
    reload_result.set_result([task])
    assert runtime.run_script(path.name, "# edited")["error"] is None
    assert starts == [f"{EditedTask.__module__}.EditedTask"]


def test_script_recording_capability_controls_both_recording_routes(monkeypatch):
    instance = SimpleNamespace(device_manager=SimpleNamespace(), task_executor=SimpleNamespace(get_all_tasks=list))
    app = create_web_app({}, ok_instance=instance)
    runtime = app.state.runtime

    async def probe():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            url = "/api/scripts-record"
            assert (await client.get(url + "/capabilities")).json() == {"available": False}
            for action in ("start", "stop"):
                result = await client.post(url + "/" + action, json={})
                assert result.status_code == 501 and "Windows" in result.json()["detail"]

            started = []
            instance.device_manager.get_hwnd_name = lambda: "test window"
            recorder = SimpleNamespace(is_recording=False, start=started.append)
            monkeypatch.setattr(runtime, "script_recorder", lambda: recorder)
            assert (await client.get(url + "/capabilities")).json() == {"available": True}
            assert (await client.post(url + "/start")).json()["recording"] is True
            assert started == ["test window"]

    asyncio.run(probe())
