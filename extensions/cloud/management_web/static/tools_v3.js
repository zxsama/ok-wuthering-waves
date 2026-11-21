const zh = {
    Log: "日志", Diagnosis: "诊断", "All Levels": "全部级别", "Filter logs...": "筛选日志…",
    Pause: "暂停", Resume: "继续", Clear: "清空视图", "Jump to latest": "跳至最新",
    Download: "下载", "No matching records": "没有匹配的日志", "No log records": "暂无日志",
    "Could not load logs": "读取日志失败", lines: "行", Paused: "已暂停", Recent: "最近截图",
    Screenshots: "截图", "Capture Screenshot": "截取画面", "Thread snapshot": "线程快照",
    Boxes: "标框", Original: "原图", "Open image": "打开图片", Refresh: "刷新",
    "No screenshot yet. Capture one to see it here.": "暂无截图。截取游戏画面后可在此查看。",
    "Start the cloud game first": "进入云游戏后可截图或识别文字。",
    "The executor is busy": "任务执行器正忙", "Could not load screenshots": "读取截图失败",
    "Could not load image": "图片加载失败", "Screenshot saved": "截图已保存",
    "OCR results": "识别结果", "No text found": "未识别到文字", Close: "关闭",
    Threads: "线程", "Diagnosis action failed": "诊断操作失败", "No game frame is available": "当前没有可用的游戏画面",
    "OCR could not finish": "文字识别未能完成", "Could not save screenshot": "保存截图失败",
    "Loading…": "加载中…", "No task is available for OCR": "当前没有可用于识别的任务",
};

function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}

function button(label, click) {
    const node = element("button", label);
    node.type = "button";
    node.addEventListener("click", click);
    return node;
}

function download(text, filename) {
    const url = URL.createObjectURL(new Blob([text], {type: "text/plain;charset=utf-8"}));
    const link = element("a");
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function getPreference(key) {
    try { return localStorage.getItem(key); } catch { return null; }
}

function savePreference(key, value) {
    try { localStorage.setItem(key, value); } catch { /* Browser storage may be disabled. */ }
}

function mountLog(page, api, t, signal) {
    const levels = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
    const header = element("div", undefined, "dt-toolbar");
    const title = element("h1", t("Log"));
    const level = element("select");
    level.setAttribute("aria-label", t("All Levels"));
    for (const value of levels) level.add(new Option(value === "ALL" ? t("All Levels") : value, value));
    const storedLevel = getPreference("ok-ww-log-level");
    if (levels.includes(storedLevel)) level.value = storedLevel;
    const query = element("input");
    query.type = "search";
    query.placeholder = t("Filter logs...");
    query.setAttribute("aria-label", query.placeholder);
    query.value = getPreference("ok-ww-log-filter") || "";
    const viewport = element("div", undefined, "dt-log");
    viewport.tabIndex = 0;
    viewport.setAttribute("role", "region");
    viewport.setAttribute("aria-label", t("Log"));
    const status = element("small", t("Loading…"), "dt-status");

    let view = {records: [], revision: "", stream: "", end: 0};
    let visible = [];
    let clearOffset = 0;
    let paused = false;
    let reading = false;
    const atTail = () => viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 8;
    const tail = button(t("Jump to latest"), () => {
        viewport.scrollTop = viewport.scrollHeight;
        tail.hidden = true;
    });
    tail.hidden = true;
    const pause = button(t("Pause"), () => {
        paused = !paused;
        pause.textContent = t(paused ? "Resume" : "Pause");
        pause.setAttribute("aria-pressed", String(paused));
        render();
        if (!paused) refresh();
    });
    pause.setAttribute("aria-pressed", "false");
    const clear = button(t("Clear"), () => {
        clearOffset = view.end;
        render();
    });
    const save = button(t("Download"), () => {
        download(visible.map(record => record.text).join("\n") + "\n", "ok-script.log.txt");
    });
    header.append(title, level, query, tail, pause, clear, save);
    page.append(header, viewport, status);

    function render() {
        const wasFollowing = atTail();
        const scrollTop = viewport.scrollTop;
        const text = query.value.toLocaleLowerCase().trim();
        const threshold = levels.indexOf(level.value);
        visible = view.records.filter(record => record.offset >= clearOffset &&
            levels.indexOf(record.level) >= threshold && record.text.toLocaleLowerCase().includes(text));
        const fragment = document.createDocumentFragment();
        let lines = 0;
        for (const record of visible) {
            fragment.append(element("div", record.text, "dt-record dt-" + record.level.toLowerCase()));
            lines += record.text.split("\n").length;
        }
        if (!visible.length) {
            const message = text || threshold ? "No matching records" : "No log records";
            fragment.append(element("div", t(message), "dt-empty-log"));
        }
        viewport.replaceChildren(fragment);
        viewport.scrollTop = wasFollowing ? viewport.scrollHeight : scrollTop;
        tail.hidden = atTail();
        save.disabled = !visible.length;
        status.textContent = [lines + " " + t("lines"), paused ? t("Paused") : ""].filter(Boolean).join(" · ");
    }

    async function refresh() {
        if (paused || reading || signal.aborted) return;
        reading = true;
        try {
            const data = await api.request("/api/desktop/log?revision=" + encodeURIComponent(view.revision), {signal});
            if (signal.aborted || paused) return;
            if (Array.isArray(data.records)) {
                if (data.stream !== view.stream || data.end < view.end) clearOffset = 0;
                view = data;
                render();
            } else if (status.dataset.failed) {
                render();
            }
            delete status.dataset.failed;
        } catch {
            if (!signal.aborted) {
                status.dataset.failed = "true";
                status.textContent = t("Could not load logs");
            }
        } finally {
            reading = false;
        }
    }
    level.addEventListener("change", () => { savePreference("ok-ww-log-level", level.value); render(); });
    query.addEventListener("input", () => { savePreference("ok-ww-log-filter", query.value); render(); });
    viewport.addEventListener("scroll", () => { tail.hidden = atTail(); });
    clear.disabled = save.disabled = true;
    refresh().then(() => { clear.disabled = false; });
    const interval = setInterval(refresh, 1800);
    return () => clearInterval(interval);
}

function threadDialog(page, rows, t) {
    const dialog = element("dialog", undefined, "dt-threads");
    dialog.setAttribute("aria-label", t("Thread snapshot"));
    const header = element("div", undefined, "dt-toolbar");
    const text = rows.map(thread => [thread.name, ...thread.frames.map(frame => "  " + frame)].join("\n")).join("\n\n");
    header.append(element("h2", t("Thread snapshot")),
        button(t("Download"), () => download(text, "thread-snapshot.txt")),
        button(t("Close"), () => dialog.close()));
    dialog.append(header);
    for (const thread of rows) {
        const details = element("details");
        details.append(element("summary", thread.name), element("pre", thread.frames.join("\n")));
        dialog.append(details);
    }
    dialog.addEventListener("close", () => dialog.remove());
    page.append(dialog);
    dialog.showModal();
}

function mountDiagnosis(page, api, t, signal) {
    const header = element("div", undefined, "dt-toolbar");
    const feedback = element("div", "", "dt-status");
    feedback.setAttribute("role", "status");
    const list = element("div", undefined, "dt-shot-list");
    const recent = element("aside", undefined, "dt-history");
    recent.setAttribute("aria-label", t("Recent"));
    const historyHeader = element("div", undefined, "dt-toolbar");
    historyHeader.append(element("h2", t("Recent")), button(t("Refresh"), () => refresh()));
    recent.append(historyHeader, list);
    const viewer = element("section", undefined, "dt-viewer");
    const shotTitle = element("h2", t("Screenshots"));
    const meta = element("small", "", "dt-status");
    const image = element("img");
    image.hidden = true;
    const empty = element("p", t("No screenshot yet. Capture one to see it here."));
    const frame = element("div", undefined, "dt-frame");
    frame.append(image, empty);
    const controls = element("div", undefined, "dt-toolbar");
    const open = element("a", t("Open image"), "dt-link");
    open.target = "_blank";
    open.rel = "noopener";
    let shots = [];
    let currentId = "";
    let variant = "boxed";
    const boxes = button(t("Boxes"), () => { variant = "boxed"; select(currentId); });
    const original = button(t("Original"), () => { variant = "original"; select(currentId); });
    controls.append(boxes, original, open);
    viewer.append(shotTitle, meta, frame, controls);
    const body = element("div", undefined, "dt-diagnosis-body");
    body.append(viewer, recent);
    let inGame = false;
    let taskRunning = false;
    let busy = false;
    let refreshSequence = 0;
    let latestSignature = "";
    let delayedRefresh;
    const capture = button(t("Capture Screenshot"), () => run("capture"));
    capture.className = "dt-primary";
    const ocr = button("OCR", () => run("ocr"));
    const threads = button(t("Thread snapshot"), () => run("threads"));
    header.append(element("h1", t("Diagnosis")), threads, ocr, capture);
    page.append(header, feedback, body);
    function syncControls() {
        capture.disabled = busy || !inGame;
        ocr.disabled = busy || !inGame || taskRunning;
        threads.disabled = busy;
        capture.title = inGame ? "" : t("Start the cloud game first");
        ocr.title = taskRunning ? t("The executor is busy") : capture.title;
    }

    function select(id) {
        currentId = id;
        const shot = shots.find(entry => entry.id === id);
        for (const row of list.children) row.setAttribute("aria-pressed", String(row.dataset.id === id));
        boxes.disabled = !shot?.kinds.includes("boxed");
        original.disabled = !shot?.kinds.includes("original");
        open.hidden = !shot;
        if (!shot) {
            shotTitle.textContent = t("Screenshots");
            meta.textContent = "";
            image.hidden = true;
            image.removeAttribute("src");
            empty.hidden = false;
            empty.textContent = t("No screenshot yet. Capture one to see it here.");
            return;
        }
        const kind = shot.kinds.includes(variant) ? variant : shot.kinds[0];
        const url = "/api/desktop/diagnosis/images/" + shot.id + "/" + kind + "?v=" + shot.modified;
        shotTitle.textContent = shot.title;
        const time = formatTime(shot.modified);
        meta.textContent = time;
        boxes.setAttribute("aria-pressed", String(kind === "boxed"));
        original.setAttribute("aria-pressed", String(kind === "original"));
        open.href = url;
        image.alt = shot.title + " · " + t(kind === "boxed" ? "Boxes" : "Original");
        image.onload = () => { meta.textContent = time + " · " + image.naturalWidth + "×" + image.naturalHeight; };
        image.onerror = () => { image.hidden = true; empty.hidden = false; empty.textContent = t("Could not load image"); };
        empty.hidden = true;
        image.hidden = false;
        if (image.getAttribute("src") !== url) image.src = url;
        else if (image.complete && image.naturalWidth) image.onload();
    }

    function formatTime(seconds) {
        return new Date(seconds * 1000).toLocaleString(api.language?.replace("_", "-") || undefined);
    }
    function renderHistory(id) {
        const rows = document.createDocumentFragment();
        for (const shot of shots) {
            const row = button("", () => select(shot.id));
            row.dataset.id = shot.id;
            row.append(element("span", shot.title), element("small", formatTime(shot.modified)));
            rows.append(row);
        }
        list.replaceChildren(rows);
        select(id);
    }

    async function refresh(preferred = "") {
        const sequence = ++refreshSequence;
        try {
            const data = await api.request("/api/desktop/diagnosis", {signal});
            if (signal.aborted || sequence !== refreshSequence) return;
            inGame = data.in_game;
            taskRunning = data.task_running;
            syncControls();
            if (!feedback.textContent || feedback.dataset.hint) {
                feedback.textContent = inGame ? "" : t("Start the cloud game first");
                feedback.dataset.hint = "true";
            }
            shots = data.shots;
            const id = [preferred, currentId].find(key => key && shots.some(shot => shot.id === key)) || shots[0]?.id || "";
            const signature = JSON.stringify(shots);
            if (signature !== latestSignature || id !== currentId) renderHistory(id);
            latestSignature = signature;
        } catch {
            if (!signal.aborted && sequence === refreshSequence) {
                feedback.textContent = t("Could not load screenshots");
                feedback.dataset.hint = "true";
            }
        }
    }

    async function run(action) {
        if (busy) return;
        busy = true;
        syncControls();
        try {
            const data = await api.request("/api/desktop/diagnosis/" + action, {method: "POST", signal});
            if (signal.aborted) return;
            if (action === "threads") {
                threadDialog(page, data.threads, t);
            } else {
                const message = action === "capture" ? t("Screenshot saved") :
                    data.count ? t("OCR results") + " · " + data.count : t("No text found");
                api.notify(message);
                await refresh(data.id);
                if (action === "ocr" && !signal.aborted) {
                    clearTimeout(delayedRefresh);
                    delayedRefresh = setTimeout(() => refresh(), 1000);
                }
            }
        } catch (error) {
            if (!signal.aborted) api.notify(t(error.message || "Diagnosis action failed"));
        } finally {
            busy = false;
            if (!signal.aborted) syncControls();
        }
    }
    syncControls();
    select("");
    refresh();
    const interval = setInterval(() => { if (!busy && !document.hidden) refresh(); }, 5000);
    return () => { clearTimeout(delayedRefresh); clearInterval(interval); };
}

export function mountTools(host, api, page) {
    if (!document.getElementById("ok-ww-desktop-tools-css")) {
        const link = element("link");
        link.id = "ok-ww-desktop-tools-css";
        link.rel = "stylesheet";
        link.href = new URL("./tools_v3.css", import.meta.url).href;
        document.head.append(link);
    }
    const root = element("section", undefined, "dt-page");
    host.replaceChildren(root);
    const abort = new AbortController();
    const t = text => api.language?.startsWith("zh") ? (zh[text] || api.t(text)) : api.t(text);
    const dispose = page === "log" ? mountLog(root, api, t, abort.signal) : mountDiagnosis(root, api, t, abort.signal);
    return () => { abort.abort(); dispose(); root.remove(); };
}
