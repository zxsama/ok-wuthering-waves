"""Offline runtime probe for the Docker adapter and headed Chrome in Xvfb."""

import json
import shutil
from pathlib import Path

import psutil

from docker.playwright_adapter import create_playwright_adapter


profile_dir = Path("/tmp/ok-ww-browser-smoke")
shutil.rmtree(profile_dir, ignore_errors=True)
default_dir = profile_dir / "Default"
default_dir.mkdir(parents=True)
preferences = default_dir / "Preferences"
preferences.write_text(
    json.dumps(
        {
            "profile": {"exit_type": "Crashed", "exited_cleanly": False},
            "ok_ww_smoke": {"preserve": True},
        }
    ),
    encoding="utf-8",
)

adapter = create_playwright_adapter()
adapter._prepare_profile_preferences(profile_dir)
prepared = json.loads(preferences.read_text(encoding="utf-8"))
assert prepared["profile"]["exit_type"] == "Normal"
assert prepared["profile"]["exited_cleanly"] is True
assert prepared["intl"]["accept_languages"] == "zh-CN,zh,en-US,en"
assert prepared["translate"]["enabled"] is False
assert prepared["ok_ww_smoke"] == {"preserve": True}

app_url = "data:text/html,<title>docker-browser-smoke</title>"
adapter.open(url=app_url, profile_dir=profile_dir, visible=True)
try:
    title, viewport = adapter.call_page(
        lambda page: (
            page.set_content(
            "<title>docker-browser-smoke</title>"
            "<canvas width='1280' height='720'></canvas>"
            )
            or (page.title(), page.viewport_size)
        )
    )
    assert title == "docker-browser-smoke"
    assert viewport == {"width": 1280, "height": 720}

    browser_commands = [
        process.info["cmdline"] or []
        for process in psutil.process_iter(["cmdline"])
        if any(
            argument == f"--user-data-dir={profile_dir}"
            for argument in (process.info["cmdline"] or [])
        )
    ]
    assert browser_commands
    browser_command = browser_commands[0]
    assert f"--app={app_url}" in browser_command
    assert "--kiosk" in browser_command
    assert "--test-type" in browser_command
    assert "--lang=zh-CN" in browser_command
    print(title, viewport)
finally:
    adapter.close()
