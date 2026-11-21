import os
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extensions.cloud.management_web import desktop_tools


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "screenshots"
    folder.mkdir()
    (tmp_path / "logs").mkdir()
    runtime = SimpleNamespace(ok=SimpleNamespace(config={}, screenshot=SimpleNamespace(screenshot_folder=folder)))
    state = {"state": "new", "task_running": False}
    app = FastAPI()
    app.state.runtime = runtime
    app.state.cloud_controller = SimpleNamespace(manual_game_status=lambda: state)
    desktop_tools.register_desktop_tools(app)
    with TestClient(app) as client:
        yield client, runtime, state, folder


def test_log_redacts_whole_multiline_record_before_filtering(service, tmp_path):
    client, runtime, _, _ = service
    path = tmp_path / "logs" / "ok-script.log"
    assert client.get("/api/desktop/log").json()["records"] == []
    path.write_text(
        "2026-11-21 12:34:56,010 INFO started\n"
        "2026-11-21 12:34:56,011 ERROR missing frame\nbacktrace line\n"
        "2026-11-21 12:34:56,012 WARNING response headers\n"
        "Set-Cookie: private content\nsecondary secret value\n"
        "2026-11-21 12:34:56,013 DEBUG https://example.invalid/?value=private\n",
        encoding="utf-8",
    )
    result = client.get("/api/desktop/log")
    assert result.status_code == 200
    assert "private" not in result.text and "secondary" not in result.text
    rows = result.json()["records"]
    assert [row["level"] for row in rows] == ["INFO", "ERROR", "WARNING", "DEBUG"]
    assert rows[1]["text"].endswith("missing frame\nbacktrace line")
    assert rows[2]["text"].endswith("[redacted]")
    assert rows[3]["text"].endswith("[redacted]")
    filtered = runtime.logs("warning", "backtrace")
    assert filtered["path"] == "ok-script.log" and filtered["line_count"] == 2
    assert filtered["text"] == rows[1]["text"] + "\n"
    assert runtime.logs("all", "private")["text"] == ""
    assert "records" not in client.get("/api/desktop/log", params={"revision": result.json()["revision"]}).json()

    with path.open("ab") as stream:
        stream.write(b"2026-11-21 12:34:56,014 INFO next\n")
    updated = client.get("/api/desktop/log", params={"revision": result.json()["revision"]}).json()
    assert updated["records"][-1]["offset"] == result.json()["end"]
    rotated = path.with_name("ok-script.1.log")
    path.rename(rotated)
    path.write_text("2026-11-21 12:45:56,014 INFO rotated\n")
    assert client.get("/api/desktop/log").json()["stream"] != updated["stream"]


def test_bounded_tail_discards_partial_private_record(service, tmp_path, monkeypatch):
    client, _, _, _ = service
    path = tmp_path / "logs" / "ok-script.log"
    path.write_text("2026-11-21 12:34:56,012 ERROR Authorization:\n" +
                    "private payload\n" * 150 + "2026-11-21 12:34:56,013 INFO next entry\n")
    monkeypatch.setattr(desktop_tools, "MAX_LOG_BYTES", 150)
    response = client.get("/api/desktop/log")
    assert response.status_code == 200 and "private" not in response.text
    assert response.json()["records"][-1]["text"].endswith("next entry")


def test_websocket_redacts_the_same_records_as_log_view(monkeypatch):
    from ok.core.events import communicate
    from extensions.cloud.management_web.app import create_web_app

    instance = SimpleNamespace(task_executor=SimpleNamespace(get_all_tasks=lambda: []), exit_event=threading.Event())
    app = create_web_app({}, ok_instance=instance)
    ready = threading.Event()
    connect = communicate.any.connect

    def subscribe(fn):
        handle = connect(fn)
        ready.set()
        return handle

    monkeypatch.setattr(communicate.any, "connect", subscribe)
    client = TestClient(app)
    url = f"/api/events?session_key={app.state.runtime.event_session_key}"
    text = "2026-11-21 12:34:56,010 INFO headers\nAuthorization: private\nnext private line"
    with client.websocket_connect(url) as ws:
        assert ready.wait(timeout=3)
        communicate.log.emit(20, text)
        assert ws.receive_json() == {"event": "log", "args": [20, "2026-11-21 12:34:56,010 INFO [redacted]"], "kwargs": {}}
        communicate.log.emit(20, "ordinary message")
        assert ws.receive_json()["args"] == [20, "ordinary message"]


def test_screenshot_pairs_and_allowed_variants(service, tmp_path):
    client, _, _, folder = service
    prefix = "2026-11-21_12-00-00-001_capture"
    content = desktop_tools.PNG_HEADER + b"picture"
    for kind in ("boxed", "original"):
        (folder / (prefix + "_" + kind + ".png")).write_bytes(content)
    (folder / "legacy.png").write_bytes(content)
    os.utime(folder / "legacy.png", (0, 0))
    (folder / "ignored.txt").write_text("private file")
    (folder / "nested").mkdir()
    (folder / "nested" / "hidden.png").write_bytes(content)
    first, legacy = client.get("/api/desktop/diagnosis").json()["shots"]
    assert first["title"] == "capture" and sorted(first["kinds"]) == ["boxed", "original"]
    assert legacy["title"] == "legacy" and legacy["kinds"] == ["original"]
    assert str(tmp_path) not in str(first)
    image_url = f"/api/desktop/diagnosis/images/{first['id']}"
    image = client.get(image_url + "/original")
    assert image.content == content and image.headers["cache-control"] == "no-store"
    assert client.get(image_url + "/other").status_code == 404
    assert client.get("/api/desktop/diagnosis/images/legacy/original").status_code == 404
    (folder / (prefix + "_boxed.png")).write_bytes(b"not an image")
    assert client.get(image_url + "/boxed").status_code == 404


def test_artifact_links_are_ignored(service, tmp_path):
    client, _, _, folder = service
    private = tmp_path / "private.png"
    private.write_bytes(desktop_tools.PNG_HEADER + b"local only")
    try:
        (folder / "linked_original.png").symlink_to(private)
        (tmp_path / "logs" / "ok-script.log").symlink_to(private)
    except OSError:
        pytest.skip("This workspace cannot create symlinks")
    assert client.get("/api/desktop/diagnosis").json()["shots"] == []
    assert client.get("/api/desktop/log").json()["records"] == []


def test_capture_writes_real_paired_images_and_handles_closed_session(service, monkeypatch):
    import numpy as np
    from ok import og
    from ok.core.screenshot import Screenshot
    from PIL import ImageFont

    client, runtime, state, folder = service
    screenshot = object.__new__(Screenshot)
    screenshot.screenshot_folder = folder
    screenshot.pil_font = ImageFont.load_default()
    box = SimpleNamespace(x=10, y=12, width=20, height=10, confidence=0.93, name="item")
    screenshot.ui_dict = {"test": ([box], 0, "red")}
    runtime.ok.screenshot = screenshot
    monkeypatch.setattr(og, "config", {}, raising=False)
    def get_frame(*, busy_wait):
        assert busy_wait > 0
        return np.zeros((100, 128, 3), dtype="uint8")

    runtime.device_manager = SimpleNamespace(capture_method=SimpleNamespace(get_frame=get_frame))
    url = "/api/desktop/diagnosis/capture"
    assert client.post(url).status_code == 409
    state["state"] = "in_game"
    response = client.post(url)
    assert response.status_code == 200, response.text
    shot, = client.get("/api/desktop/diagnosis").json()["shots"]
    assert shot["id"] == response.json()["id"] and sorted(shot["kinds"]) == ["boxed", "original"]
    base = f"/api/desktop/diagnosis/images/{shot['id']}"
    assert client.get(base + "/original").content != client.get(base + "/boxed").content


def test_ocr_runs_on_executor_and_only_returns_count(service):
    client, runtime, state, _ = service
    state["state"] = "in_game"
    calls = []

    def ocr(**kwargs):
        calls.append(kwargs)
        return ["private recognized text"]

    def submit(fn, **kwargs):
        assert kwargs["name"] == "diagnosis_ocr"
        future = Future()
        future.set_result(fn())
        return SimpleNamespace(future=future)

    task = SimpleNamespace(ocr=ocr)
    runtime.executor = SimpleNamespace(get_all_tasks=lambda: [task], submit=submit)
    url = "/api/desktop/diagnosis/ocr"
    assert client.post(url).json() == {"count": 1}
    assert calls == [{"screenshot": True}]
    state["task_running"] = True
    assert client.post(url).status_code == 409
    assert len(calls) == 1


def test_thread_snapshot_contains_frames_without_local_values(service):
    client, _, _, _ = service
    local_value = "thread-local-private-value"
    response = client.post("/api/desktop/diagnosis/threads")
    rows = response.json()["threads"]
    assert rows and any(row["frames"] for row in rows)
    assert local_value not in response.text
    assert all("/" not in frame and "\\" not in frame for row in rows for frame in row["frames"])
    assert client.post("/api/desktop/diagnosis/unsupported").status_code == 404
