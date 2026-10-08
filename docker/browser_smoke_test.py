"""Offline runtime probe for the Docker adapter and headed Chrome in Xvfb."""

import json
import shutil
import time
from pathlib import Path

import psutil
from PIL import ImageGrab

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
                "<style>html,body{margin:0;overflow:hidden;background:#192d41;}"
                ".edge{position:fixed;}"
                ".top{top:0;left:0;width:100%;height:8px;background:#ff0000;}"
                ".bottom{bottom:0;left:0;width:100%;height:8px;background:#00ff00;}"
                ".left{top:8px;bottom:8px;left:0;width:8px;background:#ffff00;}"
                ".right{top:8px;bottom:8px;right:0;width:8px;background:#0000ff;}"
                "</style><div class='edge top'></div><div class='edge bottom'></div>"
                "<div class='edge left'></div><div class='edge right'></div>"
            )
            or (page.title(), page.viewport_size)
        )
    )
    assert title == "docker-browser-smoke"
    assert viewport == {"width": 1280, "height": 720}

    def window_geometry(page):
        session = page.context.new_cdp_session(page)
        try:
            bounds = session.send("Browser.getWindowForTarget")["bounds"]
            dimensions = page.evaluate("() => [innerWidth, innerHeight, outerWidth, outerHeight]")
            return bounds, dimensions
        finally:
            session.detach()

    bounds, dimensions = adapter.call_page(window_geometry)
    assert bounds == {"left": 0, "top": 0, "width": 1280, "height": 720, "windowState": "fullscreen"}
    assert dimensions == [1280, 720, 1280, 720]
    # Check the actual X11 desktop that x11vnc transmits. A page screenshot can
    # contain the whole emulated viewport even when the native window clips it.
    expected_edges = {
        (640, 0): (255, 0, 0),
        (640, 719): (0, 255, 0),
        (0, 360): (255, 255, 0),
        (1279, 360): (0, 0, 255),
    }
    deadline = time.monotonic() + 5
    while True:
        desktop = ImageGrab.grab(xdisplay=":99")
        if desktop.size == (1280, 720) and all(
            desktop.getpixel(point)[:3] == color for point, color in expected_edges.items()
        ):
            break
        assert time.monotonic() < deadline, "native desktop clips the browser viewport"
        time.sleep(0.1)

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
    print(title, viewport, "native-fullscreen=ok desktop-edges=ok")
finally:
    adapter.close()
