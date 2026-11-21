"""Offline runtime probe for the Docker adapter and headed Chrome in Xvfb."""

import json
import os
import shutil
import time
from pathlib import Path
from threading import Event

import psutil
from PIL import ImageGrab

from docker.playwright_adapter import create_playwright_adapter
from extensions.cloud.ok_bridge import CloudDeviceManager


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
width, height = adapter.viewport
screen_width, screen_height = map(int, (os.getenv("CLOUD_SCREEN") or "1280x720x24").split("x")[:2])
adapter._prepare_profile_preferences(profile_dir)
prepared = json.loads(preferences.read_text(encoding="utf-8"))
assert prepared["profile"]["exit_type"] == "Normal"
assert prepared["profile"]["exited_cleanly"] is True
assert prepared["intl"]["accept_languages"] == "zh-CN,zh,en-US,en"
assert prepared["translate"]["enabled"] is False
assert prepared["ok_ww_smoke"] == {"preserve": True}

app_url = ("data:text/html,<title>docker-browser-smoke</title>"
           "<script>window.bootSize=[innerWidth,innerHeight,screen.width,screen.height]</script>")
adapter.open(url=app_url, profile_dir=profile_dir, visible=True)
try:
    assert adapter.call_page(lambda page: page.evaluate("bootSize")) == [width, height, width, height]
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
    assert viewport == {"width": width, "height": height}

    def window_geometry(page):
        session = page.context.new_cdp_session(page)
        try:
            bounds = session.send("Browser.getWindowForTarget")["bounds"]
            dimensions = page.evaluate("() => [innerWidth, innerHeight, screen.width, screen.height]")
            return bounds, dimensions
        finally:
            session.detach()

    bounds, dimensions = adapter.call_page(window_geometry)
    assert bounds == {"left": 0, "top": 0, "width": screen_width, "height": screen_height, "windowState": "fullscreen"}, bounds
    assert dimensions == [width, height, width, height], dimensions
    assert width * 9 == height * 16
    # Check the actual X11 desktop that x11vnc transmits. A page screenshot can
    # contain the whole emulated viewport even when the native window clips it.
    expected_edges = {
        (width // 2, 0): (255, 0, 0),
        (width // 2, height - 1): (0, 255, 0),
        (0, height // 2): (255, 255, 0),
        (width - 1, height // 2): (0, 0, 255),
    }
    deadline = time.monotonic() + 5
    while True:
        desktop = ImageGrab.grab(xdisplay=":99")
        if desktop.size == (screen_width, screen_height) and all(
            desktop.getpixel(point)[:3] == color for point, color in expected_edges.items()
        ):
            break
        assert time.monotonic() < deadline, "native desktop clips the 16:9 browser viewport"
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
    assert f"--window-size={screen_width},{screen_height}" in browser_command

    manager = CloudDeviceManager(adapter, exit_event=Event())
    frame = manager.capture_method.get_frame()
    assert frame.shape == (height, width, 3)
    for (x, y), color in expected_edges.items():
        assert tuple(frame[y, x, ::-1]) == color

    # Render a synthetic stream through both Welink player elements. A video
    # has real intrinsic dimensions and Chromium draws its own letterboxing.
    def install_player(page, *, video, native, box):
        page.set_content("<style>*{margin:0} body{background:black} video,canvas{position:fixed}</style>")
        page.evaluate("""({video, native, box}) => {
            const source = document.createElement('canvas');
            [source.width, source.height] = native;
            const ctx = source.getContext('2d');
            ctx.fillStyle = 'lime';
            ctx.fillRect(0, 0, ...native);
            ctx.fillStyle = 'red';
            ctx.fillRect(0, 0, native[0] / 8, native[1]);
            ctx.fillStyle = 'blue';
            ctx.fillRect(native[0] * 7 / 8, 0, native[0] / 8, native[1]);
            const surface = video ? document.createElement('video') : source;
            surface.id = video ? 'gmsdk-video-element' : 'sr-canvas';
            surface.style.cssText = 'left:' + box[0] + 'px;top:' + box[1] + 'px;width:' +
                box[2] + 'px;height:' + box[3] + 'px;object-fit:' + (video ? 'contain' : 'fill');
            document.body.append(surface);
            if (video) {
                surface.muted = true;
                surface.requestVideoFrameCallback(() => window.streamReady = true);
                window.streamReady = false;
                surface.srcObject = source.captureStream(10);
                surface.play();
                const timer = setInterval(() => {
                    ctx.fillRect(native[0] * 7 / 8, 0, native[0] / 8, native[1]);
                    if (window.streamReady) clearInterval(timer);
                }, 50);
            }
            surface.onpointerdown = e => window.downAt = [e.clientX, e.clientY];
            surface.onpointerup = e => window.upAt = [e.clientX, e.clientY];
            surface.onwheel = e => window.wheelAt = [e.clientX, e.clientY];
        }""", {"video": video, "native": native, "box": box})
        if video:
            page.wait_for_function("window.streamReady", timeout=5000)

    for video, native, box in [
        (False, (width, height), (0, 0, width, height)),
        (False, (640, 480), (43, 31, min(640, width - 43), min(480, height - 31))),
        (True, (640, 360), (25, 41, min(680, width - 25), min(550, height - 41))),
        (True, (480, 360), (37, 19, min(800, width - 37), min(450, height - 19))),
    ]:
        adapter.call_page(lambda page: install_player(page, video=video, native=native, box=box))
        x, y, w, h = box
        if video:
            scale = min(w / native[0], h / native[1])
            x += (w - native[0] * scale) / 2
            y += (h - native[1] * scale) / 2
            w, h = native[0] * scale, native[1] * scale
        # DOM edges can fall on half pixels. Match Chromium's positive rounding.
        left, top = int(x + 0.5), int(y + 0.5)
        w = min(width, int(x + w + 0.5)) - left
        h = min(height, int(y + h + 0.5)) - top
        frame = manager.capture_method.get_frame()
        assert frame.shape == (h, w, 3), (frame.shape, box, native)
        for tx, ty, channel in [(3, 3, 2), (w // 2, h // 2, 1), (w - 4, h - 4, 0)]:
            assert frame[ty, tx, channel] > 225, (video, frame[ty, tx])
            manager.interaction.click(tx, ty)
            assert adapter.call_page(lambda page: page.evaluate("window.downAt")) == [left + tx, top + ty]

    # Shift and resize the last player between a frame and its gesture.
    adapter.call_page(lambda page: page.evaluate("""() => {
        const player = document.querySelector('video');
        Object.assign(player.style, {left: '12px', top: '20px', width: '240px', height: '180px'});
    }"""))
    manager.interaction.swipe(0, 0, w // 2, h // 2, duration=0)
    assert adapter.call_page(lambda page: page.evaluate("[downAt, upAt]")) == [[12, 20], [132, 110]]
    manager.interaction.scroll(w // 2, h // 2, 1)
    adapter.call_page(lambda page: page.wait_for_function("window.wheelAt", timeout=3000))
    assert adapter.call_page(lambda page: page.evaluate("wheelAt")) == [132, 110]
    frame = manager.capture_method.get_frame()
    assert frame.shape == (180, 240, 3)
    manager.interaction.mouse_down(120, 90)
    manager.interaction.mouse_up()
    assert adapter.call_page(lambda page: page.evaluate("[downAt, upAt]")) == [[132, 110], [132, 110]]
    def enable_hidpi(page):
        session = page.context.new_cdp_session(page)
        session.send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 2, "mobile": False,
        })
        assert page.evaluate("devicePixelRatio") == 2

    adapter.call_page(enable_hidpi)
    frame = manager.capture_method.get_frame()
    assert frame.shape == (180, 240, 3) and frame[5, 5, 2] > 225
    manager.interaction.click(5, 5)
    assert adapter.call_page(lambda page: page.evaluate("downAt")) == [17, 25]
    manager.close()
    print(title, viewport, "native-fullscreen=ok desktop-edges=ok canvas/video-capture-and-input=ok")
finally:
    adapter.close()
