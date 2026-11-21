import asyncio
from types import SimpleNamespace

import httpx

from extensions.cloud.management_web.app import TASK_ARTWORK_DIR, create_web_app


class Task:
    def __init__(self, name, group=None):
        self.name = name
        self.group_name = group
        self.icon = str(TASK_ARTWORK_DIR / "daily.png")
        self.group_icon = str(TASK_ARTWORK_DIR / "category-daily.png")
        self.enabled = self.running = self.paused = False
        self.config = {"Dependent": 2.5, "Mode": True, "Count": 1, "_internal": 3}
        self.default_config = dict(self.config)
        self.config_type = {"Mode": {"sub_configs": {True: ["Dependent"]}}, "Count": {"min": 0, "max": 10}}
        self.first_run_alert = "First run"

    def disable(self):
        self.enabled = False

    def unpause(self):
        self.paused = False


def make_app():
    manual = Task("Daily", "Daily Activities")
    trigger = Task("Auto")
    hidden = Task("Hidden")
    hidden.visible = False
    imported = Task("Script", "Imported")
    tasks = [manual, hidden, imported, trigger]
    executor = SimpleNamespace(onetime_tasks=tasks[:3], trigger_tasks=[trigger],
                               get_all_tasks=lambda: tasks, waiting_for_task=lambda task: None)
    for task in tasks:
        task.executor = executor
    queued = {manual}
    controller = SimpleNamespace(is_task_pending=queued.__contains__, cancel_task=queued.remove)
    config = {
        "task_category_icons": {"onetime": str(TASK_ARTWORK_DIR / "category-onetime.png")},
        "task_category_colors": {"group:Daily Activities": {"light": "#2668AA", "dark": "#8ABCF0"}},
    }
    ok = SimpleNamespace(config=config, task_executor=executor, headless_app=SimpleNamespace(start_controller=controller),
                         task_manager=SimpleNamespace(imported_scripts={"one": {"script_name": "Imported", "tasks": [imported]}}),
                         get_task=lambda name: (next(task for task in tasks if task.name == name), name == trigger.name))
    return create_web_app(config, ok_instance=ok)


def test_task_page_maps_desktop_order_groups_status_and_config_controls():
    app = make_app()

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            page = (await client.get("/api/tasks/page")).json()
            assert [task["name"] for task in page["tasks"]] == ["Daily", "Auto"]
            assert list(page["categories"]) == ["all", "onetime", "trigger", "group:Daily Activities"]
            assert page["categories"]["group:Daily Activities"] == {
                "name": "Daily Activities", "icon": "/task-artwork/category-daily.png",
                "color": {"light": "#2668AA", "dark": "#8ABCF0"},
            }
            daily, trigger = page["tasks"]
            assert daily["pending"] and not trigger["pending"] and trigger["trigger"]
            assert daily["first_run_alert"] == "First run"
            assert daily["resettable"]
            assert [field["key"] for field in daily["config"]] == ["Mode", "Dependent", "Count"]
            assert daily["config"][1]["sub_config"] and daily["config"][1]["step"] == 0.1
            assert daily["config"][2]["minimum"] == 0 and daily["config"][2]["maximum"] == 10
            assert (await client.get(daily["icon"])).headers["content-type"] == "image/png"
            assert (await client.get("/task-artwork/sources.json")).status_code == 404
            assert (await client.post("/api/tasks/Daily/config", json={"key": "Mode", "value": False})).is_success
            updated = (await client.get("/api/tasks/page")).json()["tasks"][0]
            assert [field["key"] for field in updated["config"]] == ["Mode", "Count"]
            assert len((await client.get("/api/tasks")).json()) == 4

    asyncio.run(check())


def test_first_run_confirmation_and_pending_stop_still_use_installed_runtime_hooks():
    app = make_app()
    runtime = app.state.runtime
    original_action = runtime.task_action
    calls = []

    def action(name, kind):
        calls.append((name, kind))
        return original_action(name, kind)

    runtime.task_action = action
    runtime.start_task = lambda name: calls.append((name, "start")) or {}

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.post("/api/tasks/Daily/action", json={"action": "acknowledge"})).is_success
            assert (await client.post("/api/tasks/Daily/start")).is_success
            assert (await client.post("/api/tasks/Daily/action", json={"action": "stop"})).is_success
            assert calls == [("Daily", "acknowledge"), ("Daily", "start"), ("Daily", "stop")]
            task = (await client.get("/api/tasks/page")).json()["tasks"][0]
            assert task["first_run_alert"] is None and not task["pending"]

    asyncio.run(check())


def test_task_pages_publish_only_account_counts_and_report_cloud_run_capability():
    app = make_app()
    owner = type("MultiAccountDailyTask", (Task,), {"__module__": "src.task.MultiAccountDailyTask"})("Accounts")
    runtime = app.state.runtime
    owner.executor = runtime.executor
    owner.info = {"All Accounts": ["private-id"], "Completed": {"private-id"}, "Error": "private-id"}
    runtime.executor.onetime_tasks.append(owner)
    runtime.executor.get_all_tasks = lambda: runtime.executor.onetime_tasks + runtime.executor.trigger_tasks

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            for path in ("/api/tasks", "/api/tasks/page"):
                response = await client.get(path)
                assert response.is_success and "private-id" not in response.text
            native = (await client.get("/api/tasks/page")).json()["tasks"]
            assert next(t for t in native if t["name"] == "Accounts")["run_unavailable"] is None
            app.state.cloud_controller = object()
            cloud = (await client.get("/api/tasks/page")).json()["tasks"]
            task = next(t for t in cloud if t["name"] == "Accounts")
            assert task["info"] == {"All Accounts": 1, "Completed": 1}
            assert task["run_unavailable"]

    asyncio.run(check())


def test_imported_task_page_uses_live_namespace_membership_and_keeps_legacy_api():
    app = make_app()
    runtime = app.state.runtime
    executor = runtime.executor
    package = runtime.task_manager.imported_scripts["one"]
    source = package["tasks"][0]
    trigger = type("ImportedTrigger", (Task,), {})("Auto", "Imported")
    trigger.executor = executor
    executor.trigger_tasks.append(trigger)
    package["tasks"] = [trigger, source]

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            nav = (await client.get("/api/navigation")).json()
            assert nav["tasks"] and nav["triggers"] and nav["tasks_page"]
            assert nav["imported_task_pages"] == [{"id": "one", "name": "Imported"}]
            page = (await client.get("/api/tasks/page", params={"script": "one"})).json()
            assert [t["name"] for t in page["tasks"]] == ["Script", "Auto"]
            assert [t["trigger"] for t in page["tasks"]] == [False, True]
            main = (await client.get("/api/tasks/page")).json()["tasks"]
            assert [t["name"] for t in main] == ["Daily", "Auto"]
            assert main[1]["id"] != page["tasks"][1]["id"]
            assert page["tasks"][1]["id"].endswith(".ImportedTrigger")
            assert (await client.get("/api/tasks/page", params={"script": "missing"})).status_code == 404

            runtime.task_manager.imported_scripts.clear()
            assert (await client.get("/api/navigation")).json()["imported_task_pages"] == []
            assert (await client.get("/api/tasks/page", params={"script": "one"})).status_code == 404

    asyncio.run(check())


def test_edit_link_selects_the_registered_managed_script(tmp_path):
    app = make_app()
    runtime = app.state.runtime
    manager = runtime.task_manager
    manager.task_folder = tmp_path / "scripts"
    manager.task_folder.mkdir()
    script = manager.task_folder / "Second.py"
    script.write_text("selected = True", encoding="utf-8")
    external = tmp_path / script.name
    external.write_text("other = True", encoding="utf-8")
    task = runtime.executor.onetime_tasks[0]
    task.is_custom = True
    manager.task_map = {task: [script, None]}

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            item = (await client.get("/api/tasks/page")).json()["tasks"][0]
            assert item["script_file"] == "Second.py"
            assert (await client.get("/api/scripts/" + item["script_file"])).json()["code"] == "selected = True"
            manager.task_map[task] = [external, None]
            item = (await client.get("/api/tasks/page")).json()["tasks"][0]
            assert item["script_file"] is None

    asyncio.run(check())
