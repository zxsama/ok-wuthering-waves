"""Browser-level smoke test for the persistent Docker management page."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("OK_WW_MANAGEMENT_URL", "http://127.0.0.1:8765")
ARTIFACT_DIR = Path(os.environ.get("OK_WW_UI_ARTIFACT_DIR", "/tmp/ok-ww-ui-smoke"))


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(BASE_URL, wait_until="networkidle", timeout=60_000)
        main_area = page.locator("main")
        nav = page.locator("#workspace-nav")

        def open_page(page_id):
            nav.locator(f'button[data-page="{page_id}"]').click()

        def screenshot(name):
            page.screenshot(path=ARTIFACT_DIR / f"{name}.png", animations="disabled")

        assert page.evaluate("localStorage.getItem('ok-script-language')") == "zh_CN"
        expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
        for label in ("任务", "角色代码", "计划任务", "多账号", "日志", "诊断"):
            expect(nav.get_by_role("button", name=label, exact=True)).to_be_visible()
        for label in ("启动云游戏", "更新 Git / Docker", "启动执行器"):
            expect(page.get_by_role("button", name=label, exact=True)).to_be_visible()
        expect(page.locator("#okww-docker-version")).to_have_text(re.compile(r".+"))
        page.locator("#connection").click()
        expect(page.locator("#device-dialog select")).to_have_count(3)
        page.keyboard.press("Escape")

        response = context.request.get(f"{BASE_URL}/api/tasks/page")
        assert response.ok
        tasks = response.json()["tasks"]
        rows = main_area.locator(".tv3-row")
        expect(rows).to_have_count(len(tasks))
        assert any(task["trigger"] for task in tasks)
        assert any(task["group_name"] for task in tasks)
        expect(main_area).to_contain_text("启动游戏, 登录, 领月卡")
        for key in response.json()["categories"]:
            control = main_area.locator(f'button[data-filter="{key}"]')
            if key == "all":
                continue
            control.click()
            if key in ("onetime", "trigger"):
                count = sum(task["trigger"] == (key == "trigger") for task in tasks)
            else:
                count = sum(task["group_name"] == key.removeprefix("group:") for task in tasks)
            expect(rows).to_have_count(count)
        main_area.locator('button[data-filter="all"]').click()
        search = main_area.locator(".tv3-search")
        search.fill("每日任务")
        row = rows.filter(has_text="启动游戏, 登录, 领月卡")
        row.locator(".tv3-select").click()
        expect(main_area.locator(".tv3-row[data-selected=true]")).to_contain_text("每日任务")
        search.fill("")
        screenshot("tasks")

        open_page("tab:character-code")
        expect(main_area.locator('button[data-action="create"]')).to_have_text("创建队伍")
        screenshot("character-code")
        open_page("schedule")
        expect(main_area).to_contain_text("📅 每日任务")
        screenshot("schedule")
        main_area.get_by_role("button", name="创建任务", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text("每天")
        expect(dialog.get_by_label("任务完成后自动退出")).to_be_checked()
        email = dialog.get_by_label("任务完成后发送邮件汇报")
        expect(email).to_be_checked()
        email.uncheck()
        screenshot("schedule-create")
        # Inspect the real form submission without scheduling work on the host.
        existing = context.request.get(f"{BASE_URL}/api/schedule").json()
        posted = []
        def collect(route):
            posted.append(route.request.post_data_json)
            route.fulfill(json=existing)
        page.route("**/api/schedule", collect)
        dialog.locator("button[type=submit]").click()
        expect(dialog).to_have_count(0)
        assert posted[0]["email_report"] is False and posted[0]["trigger_type"] == "Daily"
        page.unroute("**/api/schedule", collect)
        main_area.get_by_role("button", name="修改", exact=True).first.click()
        expect(page.get_by_label("任务完成后发送邮件汇报")).to_be_checked(checked=existing["tasks"][0]["email_report"])
        page.get_by_role("button", name="取消", exact=True).click()

        open_page("accounts")
        expect(main_area.get_by_role("button", name="新增账号")).to_be_visible()
        open_page("log")
        expect(main_area.get_by_role("button", name="下载")).to_be_visible()
        open_page("diagnosis")
        main_area.get_by_role("button", name="线程快照").click()
        expect(page.get_by_role("dialog")).to_contain_text("线程")
        page.keyboard.press("Escape")
        open_page("settings")
        expect(main_area).to_contain_text("应用主题")
        open_page("about")
        expect(main_area).to_contain_text("OK-WW")
        for page_id in ("script", "templates", "group:Notification"):
            open_page(page_id)
            expect(main_area).not_to_be_empty()
        page.evaluate("localStorage.setItem('ok-script-language', 'en_US')")
        page.reload(wait_until="networkidle")
        expect(nav.get_by_role("button", name="Tasks", exact=True)).to_be_visible()
        open_page("schedule")
        main_area.get_by_role("button", name="Create Task", exact=True).click()
        expect(page.get_by_role("dialog")).to_contain_text("Auto Exit After Task")
        expect(page.get_by_role("dialog")).to_contain_text("Send Email Report After Task")
        page.get_by_role("button", name="Cancel", exact=True).click()

        page.evaluate("localStorage.setItem('ok-script-language', 'zh_CN')")
        page.reload(wait_until="networkidle")
        page.set_viewport_size({"width":390, "height":844})
        page.locator("#nav-toggle").click()
        open_page("tasks")
        expect(nav).to_be_hidden()
        expect(main_area.locator(".tv3-row").first).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot("tasks-mobile")
        assert not errors, errors
        print(json.dumps({"tasks":len(tasks),"pages":nav.locator("[data-page]").count(),
                          "screenshots":str(ARTIFACT_DIR),"page_errors":errors}))
        browser.close()


if __name__ == "__main__":
    main()
