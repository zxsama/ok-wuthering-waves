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
