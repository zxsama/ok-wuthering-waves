// Task presentation and state transitions follow ok-script's Qt TasksPage.
const zh = {
    Tasks: "任务", All: "全部", "On-demand": "手动", Triggers: "触发任务", Settings: "设置", Info: "信息",
    Value: "值", "Reset Config": "重置配置", "Run task": "运行任务", Start: "启动", Stop: "停止", Pause: "暂停",
    Resume: "继续", Queued: "排队中", Paused: "已暂停", Running: "运行中", Enabled: "已启用",
    "Search tasks": "搜索任务", "Select a task to see its settings.": "选择任务以查看其设置。",
    "No matching tasks.": "没有匹配的任务。", "This task has no settings.": "此任务没有设置项。",
    "Time Elapsed": "已运行", "Waiting for {name}": "等待 {name} 完成", "Launching game": "正在启动游戏",
    Instructions: "使用说明", Edit: "编辑", Close: "关闭", Modify: "修改", "Edit List": "编辑列表",
    "Move Up": "上移", "Move Down": "下移", Remove: "移除", Add: "添加", Confirm: "确认", Cancel: "取消",
    "Available Options": "可选项", "Search options": "搜索选项", "Configure Accounts": "配置账号",
    "Account Configuration": "账号配置", "Loading tasks…": "正在加载任务…", Retry: "重试", Alert: "提示",
    "The cloud adapter uses one signed-in browser profile and does not support switching accounts.": "当前云端适配器使用单一登录会话，暂不支持切换账号。",
};

function el(tag, className = "", content) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (content !== undefined) item.textContent = content;
    return item;
}

function button(label, callback, className = "tv3-button") {
    const item = el("button", className, label);
    item.type = "button";
    item.addEventListener("click", callback);
    return item;
}

function controlLabel(node, label) {
    node.setAttribute("aria-label", label);
    node.title = label;
    return node;
}

function switchButton(label, checked, change) {
    const item = button("", () => {
        const next = item.getAttribute("aria-checked") !== "true";
        item.setAttribute("aria-checked", String(next));
        change(next);
    }, "tv3-switch");
    item.setAttribute("role", "switch");
    item.setAttribute("aria-checked", String(checked));
    return controlLabel(item, label);
}

function splitName(name) {
    const match = name.trim().match(/^([^\p{L}\p{N}\s]+)\s+(.+)$/u);
    return match ? [match[1], match[2]] : ["", name.trim()];
}

function monogram(name) {
    const words = name.replaceAll("_", " ").trim().split(/\s+/);
    if (!words[0]) return "?";
    if (words[0].codePointAt(0) > 127) return Array.from(words[0])[0];
    return (words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0]).toUpperCase();
}

function artwork(url, colors, fallback) {
    const tile = el("span", "tv3-icon");
    tile.setAttribute("aria-hidden", "true");
    if (url && /^\/task-artwork\/[\w.%-]+$/.test(url)) {
        if (colors) {
            tile.classList.add("tv3-mask");
            tile.style.setProperty("--tv3-image", `url("${url}")`);
        } else {
            const img = el("img");
            img.alt = "";
            img.src = url;
            tile.append(img);
        }
    } else {
        tile.textContent = fallback;
    }
    for (const mode of ["light", "dark"]) {
        const color = typeof colors === "string" ? colors : colors?.[mode];
        if (color && CSS.supports("color", color)) tile.style.setProperty(`--tv3-ink-${mode}`, color);
    }
    return tile;
}

function isRunning(task) {
    return Boolean(task && !task.trigger && task.enabled && task.running);
}

function taskKey(task) {
    return task?.id || task?.name;
}

function taskStatus(task) {
    if (task.trigger) return "";
    if (!task.enabled) return task.pending ? "Queued" : "";
    if (task.paused) return "Paused";
    return task.running ? "Running" : "Queued";
}

function elapsed(start) {
    const total = Math.max(0, Math.floor(Date.now() / 1000 - start));
    const hours = Math.floor(total / 3600);
    const part = n => String(n).padStart(2, "0");
    return (hours ? `${hours}:` : "") + part(Math.floor(total / 60) % 60) + ":" + part(total % 60);
}

// Only text, line breaks and ordinary HTTP links survive the Qt rich text format.
// A template keeps markup inert, including elements referring to external resources.
function addInstructions(parent, value) {
    const template = document.createElement("template");
    template.innerHTML = String(value || "");
    function link(text, href) {
        if (!/^https?:\/\//i.test(href)) {
            parent.append(text);
            return;
        }
        const anchor = el("a", "", text);
        anchor.href = href;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        parent.append(anchor);
    }
    function copy(node) {
        if (node.nodeType === Node.TEXT_NODE) {
            for (const part of node.textContent.split(/(https?:\/\/[^\s<>"']+)/gi)) {
                if (/^https?:\/\//i.test(part)) link(part, part);
                else parent.append(part);
            }
        } else if (node.nodeType === Node.ELEMENT_NODE) {
            if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "TEMPLATE"].includes(node.tagName)) return;
            if (node.tagName === "A") return link(node.textContent, node.getAttribute("href") || "");
            if (node.tagName === "BR") parent.append(el("br"));
            else {
                node.childNodes.forEach(copy);
                if (["P", "DIV", "LI"].includes(node.tagName)) parent.append("\n");
            }
        }
    }
    template.content.childNodes.forEach(copy);
}

function dialog(page, title, content, t, onAccept) {
    const box = el("dialog", "tv3-dialog");
    const footer = el("div", "tv3-dialog-actions");
    footer.append(button(t(onAccept ? "Cancel" : "Close"), () => box.close()));
    if (onAccept) footer.append(button(t("Confirm"), () => {
        box.close();
        onAccept();
    }, "tv3-button tv3-primary"));
    const heading = el("h2", "", title);
    box.setAttribute("aria-label", title);
    box.append(heading, content, footer);
    page.append(box);
    box.addEventListener("close", () => box.remove(), {once: true});
    box.showModal();
}

function listDialog(page, field, t, commit) {
    const items = [...field.value];
    const panel = el("div", "tv3-list-edit");
    const list = el("select", "tv3-input");
    list.size = 7;
    controlLabel(list, t(field.key));
    const tools = el("div", "tv3-list-actions");
    const options = field.options;
    const up = button("↑", () => move(-1));
    const down = button("↓", () => move(1));
    const remove = button(t("Remove"), () => {
        const index = list.selectedIndex;
        if (index < 0) return;
        items.splice(index, 1);
        draw(Math.min(index, items.length - 1));
    });
    tools.append(controlLabel(up, t("Move Up")), controlLabel(down, t("Move Down")), remove);
    panel.append(list, tools);
    const choiceButtons = new Map();
    function update() {
        const i = list.selectedIndex;
        up.disabled = i < 1;
        down.disabled = i < 0 || i === items.length - 1;
        remove.disabled = i < 0;
        for (const [value, choice] of choiceButtons) choice.disabled = !field.allow_duplication && items.includes(value);
    }
    function draw(index) {
        list.replaceChildren(...items.map(value => el("option", "", options ? t(value) : value)));
        list.selectedIndex = index;
        update();
    }
    function move(delta) {
        const i = list.selectedIndex;
        if (i < 0 || i + delta < 0 || i + delta >= items.length) return;
        [items[i], items[i + delta]] = [items[i + delta], items[i]];
        draw(i + delta);
    }
    function add(value) {
        items.push(value);
        draw(items.length - 1);
    }
    list.addEventListener("change", update);
    if (Array.isArray(options)) {
        panel.append(el("strong", "", t("Available Options")));
        const choices = el("div", "tv3-options");
        if (options.length > 20) {
            const search = el("input", "tv3-input");
            search.type = "search";
            search.placeholder = t("Search options");
            controlLabel(search, search.placeholder);
            search.addEventListener("input", () => {
                const needle = search.value.trim().toLocaleLowerCase();
                for (const [key, choice] of choiceButtons) {
                    choice.hidden = !`${key} ${t(key)}`.toLocaleLowerCase().includes(needle);
                }
            });
            panel.append(search);
        }
        for (const value of options) {
            const choice = button(t(value), () => add(value));
            choices.append(choice);
            choiceButtons.set(value, choice);
        }
        panel.append(choices);
    } else {
        const row = el("div", "tv3-list-actions");
        const input = controlLabel(el("input", "tv3-input"), t("Add"));
        const submit = button(t("Add"), () => {
            if (input.value.trim()) {
                add(input.value);
                input.value = "";
                submit.disabled = true;
                input.focus();
            }
        });
        submit.disabled = true;
        input.addEventListener("input", () => { submit.disabled = !input.value.trim(); });
        input.addEventListener("keydown", event => { if (event.key === "Enter") submit.click(); });
        row.append(input, submit);
        panel.append(row);
    }
    draw(items.length ? 0 : -1);
    dialog(page, `${t("Edit List")} · ${t(field.key)}`, panel, t, () => commit(items));
}

function configControl(page, field, t, save) {
    const commit = value => save(field.key, value);
    if (field.kind === "boolean") return switchButton(t(field.key), field.value, commit);
    if (field.kind === "list") {
        const row = el("div", "tv3-list-actions");
        row.append(el("span", "tv3-value", field.value.map(v => field.options ? t(v) : v).join(", ")),
            button(t("Modify"), () => listDialog(page, field, t, commit)));
        return row;
    }
    if (field.kind === "multi_selection") {
        const choices = el("div", "tv3-options");
        for (const option of field.options) {
            const label = el("label", "tv3-choice");
            const input = el("input");
            input.type = "checkbox";
            input.checked = field.value.includes(option);
            label.append(input, t(option));
            choices.append(label);
        }
        choices.addEventListener("change", () => {
            commit(field.options.filter((_, i) => choices.children[i].firstChild.checked));
        });
        return choices;
    }
    if (field.kind === "select") {
        const select = controlLabel(el("select", "tv3-input"), t(field.key));
        const options = field.options || [];
        options.forEach(value => select.append(el("option", "", t(value))));
        select.selectedIndex = options.indexOf(field.value);
        select.addEventListener("change", () => commit(options[select.selectedIndex]));
        return select;
    }
    const numeric = ["integer", "number"].includes(field.kind);
    const input = el(field.kind === "multiline" ? "textarea" : "input", "tv3-input");
    controlLabel(input, t(field.key));
    if (numeric) {
        input.type = "number";
        input.required = true;
        input.step = field.step;
        input.min = field.minimum;
        input.max = field.maximum;
    }
    input.defaultValue = input.value = field.value ?? "";
    input.addEventListener("change", () => {
        if (input.reportValidity()) commit(numeric ? input.valueAsNumber : input.value);
    });
    if (input.tagName === "INPUT") {
        input.addEventListener("keydown", event => {
            if (event.key === "Enter" && !event.isComposing) input.blur();
        });
    }
    return input;
}

export function mountTasks(host, api, route = {}) {
    const t = value => {
        const key = String(value ?? "");
        if (api.language === "zh_CN" && Object.hasOwn(zh, key)) return zh[key];
        const translated = api.t?.(key);
        return translated || key;
    };
    const storageKey = "ok-ww.tasks.filter" + (route.script ? "." + route.script : "");
    const pagePath = "/api/tasks/page" + (route.script ? "?script=" + encodeURIComponent(route.script) : "");
    let filter = route.filter || localStorage.getItem(storageKey) || "all";
    let selected = route.task || null;
    let initialTask = route.task;
    let detailTab = "settings";
    let previousTask = null;
    let previouslyRunning = false;
    let snapshot = null;
    let disposed = false;
    let reading = false;
    let readAgain = false;
    let writeRevision = 0;
    let refreshTimer;
    const pending = new Set();
    const configWrites = new Map();
    const collapsed = new Set();
    const signatures = new Map();
    const page = el("section", "tv3");
    const top = el("header", "tv3-top");
    const filters = el("div", "tv3-filters");
    filters.setAttribute("role", "group");
    filters.setAttribute("aria-label", t("Tasks"));
    top.append(el("h1", "tv3-heading", t(route.title || "Tasks")), filters);
    const search = controlLabel(el("input", "tv3-input tv3-search"), t("Search tasks"));
    search.type = "search";
    search.placeholder = t("Search tasks");
    top.append(search);
    const message = el("div", "tv3-message", t("Loading tasks…"));
    message.setAttribute("role", "status");
    const body = el("div", "tv3-body");
    const list = el("div", "tv3-list");
    const detail = el("section", "tv3-detail");
    const header = el("div", "tv3-detail-head");
    const titleBlock = el("div", "tv3-title-block");
    const title = el("h2");
    const description = el("p");
    const statusText = el("div", "tv3-status-text");
    titleBlock.append(title, description, statusText);
    const actions = el("div", "tv3-actions");
    header.append(titleBlock, actions);
    const tabs = el("div", "tv3-tabs");
    const content = el("div", "tv3-content");
    detail.append(header, tabs, content);
    body.append(list, detail);
    page.append(top, message, body);
    host.replaceChildren(page);
    const selectedTask = () => snapshot?.tasks.find(task => taskKey(task) === selected);

    function changed(key, values, update) {
        const signature = JSON.stringify(values);
        if (signatures.get(key) === signature) return;
        signatures.set(key, signature);
        update();
    }

    function statusDetail(task) {
        const status = taskStatus(task);
        statusText.hidden = !status;
        statusText.dataset.status = status;
        const parts = [t(status)];
        if (status === "Running" && task.start_time) parts.push(`${t("Time Elapsed")} ${elapsed(task.start_time)}`);
        else if (task.pending) parts.push(t("Launching game"));
        else if (status === "Queued" && task.waiting_for) {
            parts.push(t("Waiting for {name}").replace("{name}", t(task.waiting_for)));
        }
        statusText.textContent = parts.join(" · ");
    }

    function scheduleRefresh() {
        if (disposed || refreshTimer) return;
        refreshTimer = setTimeout(() => {
            refreshTimer = null;
            refresh();
        }, 120);
    }

    async function refresh() {
        if (disposed) return;
        if (reading) {
            readAgain = true;
            return;
        }
        reading = true;
        const revision = writeRevision;
        try {
            const data = await api.request(pagePath);
            if (disposed || revision !== writeRevision || configWrites.size) return;
            snapshot = data;
            message.hidden = true;
            render();
        } catch (error) {
            if (!disposed) {
                message.hidden = false;
                message.replaceChildren(document.createTextNode(error.message || String(error)),
                    button(t("Retry"), refresh));
            }
        } finally {
            reading = false;
            if (readAgain && !disposed) {
                readAgain = false;
                scheduleRefresh();
            }
        }
    }

    function saveSetting(task, key, value) {
        const id = taskKey(task);
        const previous = configWrites.get(id) || Promise.resolve();
        const path = "/api/tasks/" + encodeURIComponent(id) + "/config";
        // Queue each edit even if the user has already selected another field or page.
        const write = previous.catch(() => {}).then(() => api.request(path, {method: "POST", body: {key, value}}));
        configWrites.set(id, write);
        writeRevision++;
        write.catch(error => {
            if (!disposed) {
                api.notify?.(error.message || String(error));
                if (selected === id) signatures.delete("form");
            }
        }).finally(() => {
            if (configWrites.get(id) !== write) return;
            configWrites.delete(id);
            writeRevision++;
            if (!disposed && !configWrites.size) refresh();
        });
    }

    async function mutate(task, endpoint, data = {}, acknowledge = false) {
        const id = taskKey(task);
        if (pending.has(id)) return;
        pending.add(id);
        render();
        const path = "/api/tasks/" + encodeURIComponent(id);
        try {
            await configWrites.get(id);
            if (acknowledge) {
                await api.request(path + "/action", {method: "POST", body: {action: "acknowledge"}});
            }
            await api.request(path + "/" + endpoint, {method: "POST", body: data});
        } catch (error) {
            if (!disposed) {
                api.notify?.(error.message || String(error));
                message.hidden = false;
                message.textContent = error.message || String(error);
            }
        } finally {
            pending.delete(id);
            writeRevision++;
            if (!disposed) {
                if (selected === id && endpoint === "config/reset") signatures.delete("form");
                await refresh();
                render();
            }
        }
    }

    function run(task) {
        if (task.run_unavailable) return;
        if (task.enabled && task.paused) return mutate(task, "action", {action: "resume"});
        if (task.first_run_alert) {
            const instructions = el("div", "tv3-instructions");
            addInstructions(instructions, t(task.first_run_alert));
            dialog(page, t("Alert"), instructions, t, () => mutate(task, "start", {}, true));
        } else {
            mutate(task, "start");
        }
    }

    function taskSwitch(task) {
        const toggle = switchButton(t(task.name), task.enabled, enabled => {
            mutate(task, "action", {action: enabled ? "enable" : "disable"});
        });
        toggle.disabled = pending.has(taskKey(task));
        return toggle;
    }

    function actionButton(task, action, compact = false) {
        const label = t(action);
        const glyphs = {Start: "▶", "Run task": "▶", Resume: "▶", Stop: "■", Pause: "Ⅱ"};
        const item = button("", () => {
            if (action === "Stop" || action === "Pause") mutate(task, "action", {action: action.toLowerCase()});
            else run(task);
        });
        const icon = el("span", "tv3-action-icon", glyphs[action]);
        icon.setAttribute("aria-hidden", "true");
        item.append(icon);
        if (!compact) item.append(label);
        if (compact) item.classList.add("tv3-square");
        else if (action === "Run task" || action === "Resume") item.classList.add("tv3-primary");
        else if (action === "Stop") item.classList.add("tv3-danger");
        const blocked = task.run_unavailable && !["Stop", "Pause"].includes(action);
        item.disabled = pending.has(taskKey(task)) || Boolean(blocked);
        controlLabel(item, compact ? `${label} · ${splitName(t(task.name))[1]}` : label);
        if (blocked) item.title = t(task.run_unavailable);
        return item;
    }

    function selectTask(name) {
        selected = name;
        render();
    }

    function renderFilters(tasks, cats) {
        const counts = {all: tasks.length, onetime: 0, trigger: 0};
        for (const task of tasks) {
            counts[task.trigger ? "trigger" : "onetime"]++;
            if (task.group_name) {
                const key = "group:" + task.group_name;
                counts[key] = (counts[key] || 0) + 1;
            }
        }
        const keys = Object.keys(cats).filter(key => !["trigger", "onetime"].includes(key) ||
            (counts.trigger && counts.onetime));
        if (!keys.includes(filter)) filter = "all";
        filters.hidden = keys.length < 2;
        changed("filters", [filter, cats, counts], () => {
            filters.replaceChildren(...keys.map(key => {
                const category = cats[key];
                const item = button("", () => {
                    filter = key;
                    localStorage.setItem(storageKey, key);
                    render();
                }, "tv3-filter");
                const glyph = {all: "☷", onetime: "ϟ", trigger: "◷"}[key];
                if (category.icon || glyph) item.append(artwork(category.icon, category.color, glyph));
                item.append(el("span", "", t(category.name)), el("span", "tv3-count", counts[key]));
                item.dataset.filter = key;
                item.setAttribute("aria-pressed", String(filter === key));
                return item;
            }));
        });
    }

    function renderList(rows, cats) {
        const listData = rows.map(task => [taskKey(task), task.name, task.description, task.trigger, task.enabled, task.running,
            task.paused, task.pending, task.waiting_for, task.icon, task.group_name, task.run_unavailable, pending.has(taskKey(task))]);
        changed("list", [filter, selected, listData, cats, [...collapsed]], () => {
            const scroll = new Map([...list.querySelectorAll(".tv3-rows")].map(n => [n.dataset.kind, n.scrollTop]));
            const groups = [
                ["onetime", rows.filter(task => !task.trigger)], ["trigger", rows.filter(task => task.trigger)],
            ].filter(([, group]) => group.length);
            list.replaceChildren();
            if (!rows.length) list.append(el("p", "tv3-empty", t("No matching tasks.")));
            for (const [kind, group] of groups) {
                const cat = cats[groups.length === 1 && filter.startsWith("group:") ? filter : kind];
                const section = el("section", "tv3-section");
                const canCollapse = filter === "all";
                const expanded = !canCollapse || !collapsed.has(kind);
                section.dataset.kind = kind;
                section.dataset.expanded = String(expanded);
                const head = canCollapse
                    ? button("", () => {
                        if (expanded) collapsed.add(kind);
                        else collapsed.delete(kind);
                        render();
                    }, "tv3-section-head")
                    : el("div", "tv3-section-head");
                head.append(artwork(cat.icon, cat.color, kind === "trigger" ? "◷" : "ϟ"),
                    el("strong", "", t(cat.name)), el("span", "tv3-count", group.length));
                if (canCollapse) {
                    head.setAttribute("aria-expanded", String(expanded));
                    const chevron = el("span", "tv3-chevron");
                    chevron.setAttribute("aria-hidden", "true");
                    head.append(chevron);
                }
                section.append(head);
                if (expanded) {
                    const inner = el("div", "tv3-rows");
                    inner.dataset.kind = kind;
                    for (const task of group) {
                        const row = el("div", "tv3-row");
                        row.dataset.selected = String(taskKey(task) === selected);
                        const pick = button("", () => selectTask(taskKey(task)), "tv3-select");
                        const [symbol, name] = splitName(t(task.name));
                        const copy = el("span", "tv3-row-copy");
                        const blurb = t(task.description);
                        copy.append(el("strong", "", name));
                        if (blurb) copy.append(el("small", "", blurb));
                        pick.title = [name, blurb].filter(Boolean).join("\n");
                        pick.setAttribute("aria-pressed", String(taskKey(task) === selected));
                        const colors = cats["group:" + task.group_name]?.color || cats[kind].color;
                        pick.append(artwork(task.icon, colors, symbol || monogram(name)), copy);
                        row.append(pick);
                        const status = taskStatus(task);
                        if (status) {
                            const badge = el("span", "tv3-pill", t(status));
                            badge.dataset.status = status;
                            if (task.waiting_for) badge.title = t("Waiting for {name}").replace("{name}", t(task.waiting_for));
                            row.append(badge);
                        }
                        const action = (task.enabled && !task.paused) || task.pending ? "Stop" : task.paused && task.enabled ? "Resume" : "Start";
                        row.append(task.trigger ? taskSwitch(task) : actionButton(task, action, true));
                        inner.append(row);
                    }
                    section.append(inner);
                }
                list.append(section);
            }
            list.querySelectorAll(".tv3-rows").forEach(n => { n.scrollTop = scroll.get(n.dataset.kind) || 0; });
        });
    }

    function showDetail(task) {
        const running = isRunning(task);
        const id = taskKey(task);
        if (id !== previousTask || (running && !previouslyRunning)) detailTab = running ? "info" : "settings";
        previouslyRunning = running;
        previousTask = id;
        header.hidden = tabs.hidden = !task;
        if (!task) {
            changed("form", [null], () => content.replaceChildren(el("p", "tv3-empty", t("Select a task to see its settings."))));
            return;
        }
        const busy = pending.has(id);
        changed("header", [id, task.name, task.description, task.instructions, task.trigger, task.enabled, running,
            task.paused, task.pending, busy, task.script_file, task.run_unavailable], () => {
            title.textContent = splitName(t(task.name))[1];
            description.textContent = [t(task.description), t(task.run_unavailable)].filter(Boolean).join("\n");
            description.hidden = !description.textContent;
            actions.replaceChildren();
            if (task.instructions) actions.append(button(t("Instructions"), () => {
                const text = el("div", "tv3-instructions");
                addInstructions(text, t(task.instructions));
                dialog(page, title.textContent, text, t);
            }));
            if (task.script_file) actions.append(button(t("Edit"), () => api.navigate("script", {file: task.script_file})));
            if (task.trigger) actions.append(el("span", "", t("Enabled")), taskSwitch(task));
            else {
                if (running && !task.paused) actions.append(actionButton(task, "Pause"));
                if (task.enabled || task.pending) actions.append(actionButton(task, "Stop"));
                if ((!task.enabled || task.paused) && !task.pending) actions.append(actionButton(task, task.enabled ? "Resume" : "Run task"));
            }
        });
        statusDetail(task);
        const entries = Object.entries(task.info || {});
        const hasInfo = Boolean(entries.length || running);
        if (!hasInfo) detailTab = "settings";
        changed("tabs", [id, detailTab, hasInfo, busy, task.resettable, task.config.length], () => {
            tabs.replaceChildren();
            for (const tab of hasInfo ? ["settings", "info"] : ["settings"]) {
                const item = button(t(tab === "info" ? "Info" : "Settings"), () => {
                    detailTab = tab;
                    render();
                }, "tv3-tab");
                item.setAttribute("aria-pressed", String(detailTab === tab));
                tabs.append(item);
            }
            if (detailTab === "settings" && task.resettable && task.config.length) {
                const reset = button(t("Reset Config"), () => mutate(task, "config/reset"), "tv3-button tv3-reset");
                reset.disabled = busy;
                tabs.append(reset);
            }
        });
        if (detailTab === "info") {
            changed("form", [id, "info", entries], () => {
                const table = el("table", "tv3-info");
                const headings = el("tr");
                headings.append(el("th", "", t("Info")), el("th", "", t("Value")));
                table.createTHead().append(headings);
                const rows = table.createTBody();
                for (const [key, value] of entries) {
                    const tr = rows.insertRow();
                    tr.insertCell().textContent = t(key);
                    tr.insertCell().textContent = typeof value === "object" ? JSON.stringify(value) : t(value);
                }
                content.replaceChildren(table);
            });
        } else {
            changed("form", [id, "settings", task.config], () => {
                const scroll = content.scrollTop;
                const oldInput = content.contains(document.activeElement) ? document.activeElement : null;
                const activeKey = oldInput?.closest("[data-key]")?.dataset.key;
                const sameTask = content.dataset.task === id;
                const fields = el("fieldset", "tv3-form");
                fields.setAttribute("aria-label", t("Settings"));
                for (const field of task.config) {
                    const row = el("div", "tv3-field");
                    row.dataset.key = field.key;
                    row.dataset.sub = String(field.sub_config);
                    const label = el("div", "tv3-field-copy");
                    label.append(el("strong", "", t(field.key)));
                    if (field.description) label.append(el("small", "", t(field.description)));
                    row.append(label, configControl(page, field, t, (key, value) => saveSetting(task, key, value)));
                    fields.append(row);
                    if (task.class_name === "MultiAccountDailyTask" && field.key === "Per-account Configuration" && field.value) {
                        const account = el("div", "tv3-field");
                        account.dataset.sub = "true";
                        account.append(el("strong", "", t("Account Configuration")), button(t("Configure Accounts"), () => api.navigate("accounts")));
                        fields.append(account);
                    }
                }
                if (fields.childElementCount) content.replaceChildren(fields);
                else content.replaceChildren(el("p", "tv3-empty", t("This task has no settings.")));
                content.dataset.task = id;
                if (sameTask && activeKey && oldInput.matches("textarea,input:not([type=checkbox]),select")) {
                    const row = [...fields.children].find(node => node.dataset.key === activeKey);
                    const input = row?.querySelector(".tv3-input");
                    if (input && input.tagName === oldInput.tagName) {
                        if (oldInput.matches("textarea,input") && oldInput.value !== oldInput.defaultValue) {
                            input.value = oldInput.value;
                            if (oldInput.selectionStart !== null) input.setSelectionRange(oldInput.selectionStart, oldInput.selectionEnd);
                        }
                        input.focus({preventScroll: true});
                    }
                }
                content.scrollTop = scroll;
            });
            const fields = content.querySelector("fieldset");
            if (fields) fields.disabled = busy;
        }
    }

    function render() {
        if (!snapshot || disposed) return;
        const {tasks, categories} = snapshot;
        if (initialTask) {
            const target = tasks.find(task => taskKey(task) === initialTask || task.name === initialTask || task.class_name === initialTask);
            if (target) selected = taskKey(target);
            if (target && !route.filter && filter !== (target.trigger ? "trigger" : "onetime") &&
                filter !== "group:" + target.group_name) filter = "all";
            initialTask = null;
        }
        renderFilters(tasks, categories);
        const query = search.value.trim().toLocaleLowerCase();
        const rows = tasks.filter(task => {
            const kind = task.trigger ? "trigger" : "onetime";
            if (filter !== "all" && filter !== kind && filter !== "group:" + task.group_name) return false;
            if (!query) return true;
            return [task.name, task.class_name, task.description, task.group_name]
                .some(value => value && `${value} ${t(value)}`.toLocaleLowerCase().includes(query));
        });
        if (!rows.some(task => taskKey(task) === selected)) {
            selected = taskKey(rows[0]) || null;
        }
        renderList(rows, categories);
        showDetail(selectedTask());
    }

    search.addEventListener("input", render);
    const unsubscribe = api.subscribe?.(event => {
        if (["task", "task_done", "task_list_updated", "executor_paused"].includes(event?.event || event?.name || event)) scheduleRefresh();
    });
    const poll = setInterval(() => { if (!document.hidden) refresh(); }, 4000);
    const tick = setInterval(() => {
        const task = selectedTask();
        if (!document.hidden && task && taskStatus(task) === "Running") statusDetail(task);
    }, 1000);
    refresh();
    return () => {
        disposed = true;
        clearTimeout(refreshTimer);
        clearInterval(poll);
        clearInterval(tick);
        if (typeof unsubscribe === "function") unsubscribe();
        page.remove();
    };
}
