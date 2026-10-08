"""Browser-level smoke test for the persistent Docker management page."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


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
        page.goto(BASE_URL, wait_until="networkidle", timeout=60_000)
        body = page.locator("body")
        body_text = body.inner_text()
        assert page.evaluate("localStorage.getItem('ok-script-language')") == "zh_CN"
        assert page.locator("html").get_attribute("lang") == "zh-CN"
        for label in ("任务", "角色代码", "计划任务"):
            assert label in body_text
        assert page.get_by_role("button", name="启动云游戏", exact=True).is_visible()
        assert page.get_by_role("button", name="更新 Git / Docker", exact=True).is_visible()
        version = page.locator("#okww-docker-version")
        assert version.is_visible()
        assert version.evaluate("node => getComputedStyle(node).position") == "absolute"

        page.get_by_role("button", name="任务", exact=True).click(timeout=5_000)
        page.wait_for_timeout(500)
        tasks_text = body.inner_text()
        assert "📅 每日任务" in tasks_text
        assert "启动游戏, 登录, 领月卡" in tasks_text
        page.screenshot(path=ARTIFACT_DIR / "tasks.png", animations="disabled", timeout=10_000)

        page.get_by_role("button", name="角色代码", exact=True).click(timeout=5_000)
        page.locator('button[data-action="create"]').wait_for(timeout=15_000)
        character_text = body.inner_text()
        assert "创建队伍" in character_text
        page.screenshot(
            path=ARTIFACT_DIR / "character-code.png",
            animations="disabled",
            timeout=10_000,
        )

        page.get_by_role("button", name="计划任务", exact=True).click(timeout=5_000)
        page.wait_for_timeout(500)
        schedule_text = body.inner_text()
        page.screenshot(path=ARTIFACT_DIR / "schedule.png", animations="disabled", timeout=10_000)
        assert "📅 每日任务" in schedule_text
        page.get_by_role("button", name="创建任务", exact=True).click(timeout=5_000)
        page.wait_for_timeout(300)
        create_schedule_text = body.inner_text()
        page.screenshot(path=ARTIFACT_DIR / "schedule-create.png", animations="disabled", timeout=10_000)
        assert "每天" in create_schedule_text
        assert "任务完成后自动退出" in create_schedule_text

        page.evaluate("localStorage.setItem('ok-script-language', 'en_US')")
        page.reload(wait_until="networkidle")
        assert page.get_by_role("button", name="Tasks", exact=True).is_visible()
        page.get_by_role("button", name=re.compile("Schedule", re.IGNORECASE)).click(timeout=5_000)
        page.get_by_role("button", name="Create Task", exact=True).click(timeout=5_000)
        page.wait_for_timeout(300)
        assert "Daily" in body.inner_text()
        assert "Auto Exit After Task" in body.inner_text()

        page.evaluate("localStorage.setItem('ok-script-language', 'zh_CN')")
        page.reload(wait_until="networkidle")
        assert page.get_by_role("button", name="任务", exact=True).is_visible()
        page.get_by_role("button", name="任务", exact=True).click(timeout=5_000)
        page.wait_for_timeout(300)
        assert "📅 每日任务" in body.inner_text()

        navigation = page.locator("nav, aside, [role=navigation]").all_inner_texts()
        buttons = page.get_by_role("button").all_inner_texts()
        links = page.get_by_role("link").all_inner_texts()
        result = {
            "title": page.title(),
            "url": page.url,
            "body": body_text[:4000],
            "tasks": tasks_text[:4000],
            "character_code": character_text[:4000],
            "schedule": schedule_text[:4000],
            "schedule_create": create_schedule_text[:4000],
            "navigation": navigation,
            "buttons": buttons,
            "links": links,
        }
        (ARTIFACT_DIR / "page.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
