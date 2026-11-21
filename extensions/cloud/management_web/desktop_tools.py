"""Cloud equivalents of the desktop log viewer and diagnostics page."""

import hashlib
import os
import re
import stat
import sys
import threading
from collections import deque
from pathlib import Path

from fastapi import HTTPException, Query, Response
from ok.device.capture_methods.base import CAPTURE_BUSY_WAIT

from .app import LOG_LEVELS, LOG_LINE_PATTERN


MAX_LOG_BYTES = 512 * 1024
MAX_LOG_RECORDS = 5000
MAX_SHOTS = 200
MAX_IMAGE_BYTES = 32 * 1024 * 1024
PNG_HEADER = b"\x89PNG\r\n\x1a\n"
PRIVATE_RECORD = re.compile(
    r"(?:password|passwd|passphrase|\bpwd\b|authorization|authentication[ _-]?code|cookie|webhook|"
    r"api\.telegram\.org/bot|api\.day\.app/|"
    r"\b(?:token|secret|credential|bearer|otp|csrf|jwt)\b|"
    r"(?:access|refresh|session|auth|client|smtp|api|private|signing)[_ -]?"
    r"(?:id|key|token|secret|pass(?:word)?)|"
    r"(?:user(?:name)?|login|email|phone|account|verification[_ -]?code)[\"']?\s*[=:]|"
    r"-----BEGIN [^-]*PRIVATE KEY|https?://[^\s]*[?@]|"
    r"\beyJ[\w-]+\.[\w-]+\.[\w-]+|"
    r"密码|口令|验证码|令牌|密钥|凭据)",
    re.IGNORECASE,
)
TIME_PREFIX = re.compile(r"^\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d(?:-\d+)?_?")
EMAIL = re.compile(r"[\w.+-]{1,64}@[\w.-]{1,253}\.\w{2,63}")


def redact_log_message(text):
    if PRIVATE_RECORD.search(text) or ("@" in text and EMAIL.search(text)):
        header = LOG_LINE_PATTERN.match(text)
        return (header.group(0) if header else "") + "[redacted]"
    return text


def _plain_entry(path, *, directory=False):
    """Reject links, junctions and special files under the configured artifact root."""
    try:
        info = path.lstat()
    except OSError:
        return False
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    return (stat.S_ISDIR if directory else stat.S_ISREG)(info.st_mode)


def _open_artifact(path):
    if not _plain_entry(path.parent, directory=True) or not _plain_entry(path):
        raise FileNotFoundError()
    before = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file = os.fdopen(os.open(path, flags | getattr(os, "O_NONBLOCK", 0)), "rb")
    after = os.fstat(file.fileno())
    if not stat.S_ISREG(after.st_mode) or not os.path.samestat(before, after):
        file.close()
        raise FileNotFoundError()
    return file


def _log_records(raw, start):
    """Keep continuations together for filtering and for redaction of whole records."""
    records = deque(maxlen=MAX_LOG_RECORDS)
    current = []
    level = "INFO"
    offset = start
    record_start = start

    def finish():
        if current:
            message = redact_log_message("\n".join(current))
            records.append({"offset": record_start, "level": level, "text": message})

    # The first bytes of a truncated tail may belong to a preceding private record.
    can_read = start == 0
    for index, line in enumerate(raw.splitlines(keepends=True)):
        text = line.rstrip(b"\r\n").decode("utf-8", errors="replace")
        header = LOG_LINE_PATTERN.match(text)
        if header and (not start or index != 0):
            finish()
            can_read = True
            record_start = offset
            level = header.group("level")
            current = []
        if can_read:
            current.append(text)
        offset += len(line)
    finish()
    return list(records)


def _read_log_view(path, revision=""):
    try:
        with _open_artifact(path) as file:
            info = os.fstat(file.fileno())
            stream = f"{info.st_dev:x}-{info.st_ino:x}"
            current = f"{stream}-{info.st_size:x}-{info.st_mtime_ns:x}"
            view = {"stream": stream, "end": info.st_size, "modified": info.st_mtime,
                    "revision": current}
            if revision == current:
                return view
            start = max(0, info.st_size - MAX_LOG_BYTES)
            file.seek(start)
            view["records"] = _log_records(file.read(info.st_size - start), start)
            return view
    except FileNotFoundError:
        return {"stream": "", "end": 0, "modified": None, "revision": "", "records": []}


def _legacy_logs(path, level, query):
    level = level.upper()
    if level not in LOG_LEVELS:
        raise ValueError("Unknown log level")
    view = _read_log_view(path)
    query = query.strip().casefold()
    blocks = [row["text"] for row in view["records"]
              if LOG_LEVELS[row["level"]] >= LOG_LEVELS[level] and query in row["text"].casefold()]
    text = "\n".join(blocks)
    return {"path": path.name, "text": text + "\n" if blocks else "",
            "line_count": len(text.splitlines()), "modified": view["modified"]}


def _shot_id(stem):
    return hashlib.sha256(os.fsencode(stem)).hexdigest()[:32]


def _list_shots(folder):
    if not _plain_entry(folder, directory=True):
        return []
    shots = {}
    for entry in folder.iterdir():
        if entry.suffix.lower() != ".png" or not _plain_entry(entry):
            continue
        try:
            modified = entry.stat().st_mtime
        except FileNotFoundError:
            continue
        stem = entry.stem
        kind = "original"
        if stem.endswith(("_boxed", "_original")):
            stem, kind = stem.rsplit("_", 1)
        shot = shots.setdefault(stem, {"id": _shot_id(stem), "title": TIME_PREFIX.sub("", stem) or stem,
                                       "modified": modified, "files": {}})
        shot["files"][kind] = entry
        shot["modified"] = max(shot["modified"], modified)
    return sorted(shots.values(), key=lambda shot: (shot["modified"], shot["id"]), reverse=True)[:MAX_SHOTS]


def _thread_snapshot():
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    threads = []
    for ident, frame in sys._current_frames().items():
        frames = []
        while frame is not None and len(frames) < 64:
            code = frame.f_code
            frames.append(redact_log_message(f"{Path(code.co_filename).name}:{frame.f_lineno} · {code.co_name}"))
            frame = frame.f_back
        name = names.get(ident, "Thread")
        threads.append({"name": redact_log_message(name), "frames": frames[::-1]})
    return sorted(threads, key=lambda thread: thread["name"])


def _capture(runtime, folder):
    from ok.core.screenshot import screenshot_file_stem

    method = runtime.device_manager.capture_method
    frame = method.get_frame(busy_wait=CAPTURE_BUSY_WAIT) if method is not None else None
    if frame is None:
        raise HTTPException(409, "No game frame is available")
    folder.mkdir(exist_ok=True, parents=True)
    if not _plain_entry(folder, directory=True):
        raise HTTPException(409, "Screenshot storage is unavailable")
    screenshot = runtime.ok.screenshot
    saved = screenshot.generate_screen_shot(
        frame, screenshot.ui_dict, str(folder), screenshot_file_stem("manual"), True, None,
        processor=runtime.ok.config.get("screenshot_processor"),
    )
    if not saved:
        raise HTTPException(503, "Could not save screenshot")
    return {"id": _shot_id(Path(saved).stem.removesuffix("_original"))}


def _ocr(runtime):
    from ok.core.code_runner import submit_call, wait_result
    from ok.task.exceptions import ExecutorBusyException

    tasks = runtime.executor.get_all_tasks()
    if not tasks:
        raise HTTPException(409, "No task is available for OCR")
    try:
        job = submit_call(runtime.executor, lambda: tasks[0].ocr(screenshot=True), name="diagnosis_ocr")
    except ExecutorBusyException:
        raise HTTPException(409, "The executor is busy") from None
    result = wait_result(job, timeout=30)
    if result.cancelled or result.error:
        raise HTTPException(503, "OCR could not finish")
    return {"count": len(result.raw or [])}


def register_desktop_tools(app):
    runtime = app.state.runtime
    log = Path.cwd() / "logs" / "ok-script.log"
    screenshot = getattr(runtime.ok, "screenshot", None)
    folder = Path(getattr(screenshot, "screenshot_folder", None) or runtime.ok.config.get("screenshots_folder") or
                  Path.cwd() / "screenshots")
    action_lock = threading.Lock()
    runtime.logs = lambda level="ALL", query="": _legacy_logs(log, level, query)

    @app.get("/api/desktop/log")
    def log_view(revision: str = Query(default="", max_length=128)):
        return _read_log_view(log, revision)

    @app.get("/api/desktop/diagnosis")
    def diagnosis():
        shots = [{"id": shot["id"], "title": shot["title"], "modified": shot["modified"],
                  "kinds": list(shot["files"])} for shot in _list_shots(folder)]
        active = app.state.cloud_controller.manual_game_status()
        return {"shots": shots, "in_game": active["state"] == "in_game",
                "task_running": active["task_running"]}

    @app.get("/api/desktop/diagnosis/images/{shot_id}/{kind}")
    def image(shot_id: str, kind: str):
        if kind in ("original", "boxed") and re.fullmatch("[a-f0-9]{32}", shot_id):
            for shot in _list_shots(folder):
                if shot["id"] != shot_id or kind not in shot["files"]:
                    continue
                try:
                    with _open_artifact(shot["files"][kind]) as file:
                        size = os.fstat(file.fileno()).st_size
                        if size <= MAX_IMAGE_BYTES and file.read(len(PNG_HEADER)) == PNG_HEADER:
                            return Response(PNG_HEADER + file.read(size - len(PNG_HEADER)), media_type="image/png",
                                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
                except FileNotFoundError:
                    pass
                break
        raise HTTPException(404, "Screenshot not found")

    @app.post("/api/desktop/diagnosis/threads")
    def threads():
        return {"threads": _thread_snapshot()}

    @app.post("/api/desktop/diagnosis/{action}")
    def frame_action(action: str):
        if action not in ("capture", "ocr"):
            raise HTTPException(404, "Unknown diagnosis action")
        active = app.state.cloud_controller.manual_game_status()
        if active["state"] != "in_game":
            raise HTTPException(409, "Start the cloud game first")
        if (action == "ocr" and active["task_running"]) or not action_lock.acquire(blocking=False):
            raise HTTPException(409, "The executor is busy")
        try:
            return _capture(runtime, folder) if action == "capture" else _ocr(runtime)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Diagnosis action failed") from None
        finally:
            action_lock.release()
