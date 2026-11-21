import { setLocale, translate, publishTabEvent } from "./app.js";
import { mountPanel } from "./workspace_panels.js";

const $ = (id) => document.getElementById(id);
const cloud = globalThis.okwwTranslations !== undefined;
const language = setLocale(localStorage.getItem("ok-script-language") || "Auto");
if (cloud && language !== "en_US") {
  globalThis.okwwActiveTranslations =
    language === "zh_CN"
      ? globalThis.okwwTranslations
      : await request("/api/ui/translations/" + encodeURIComponent(language)).catch(() => ({}));
}
const chinese = {
  Run: "运行",
  Game: "游戏",
  Develop: "开发",
  Tasks: "任务",
  Log: "日志",
  Diagnosis: "诊断",
  Schedule: "计划任务",
  Accounts: "多账号",
  Connection: "设备连接",
  Navigation: "导航",
  Idle: "待机",
  Offline: "离线",
  Connecting: "连接中",
  "Start executor": "启动执行器",
  "Pause executor": "暂停执行器",
  "Cancel start": "取消启动",
  "Cancel this task": "取消当前任务",
  Retry: "重试",
  "No device": "无设备",
  "Enable Boxes": "显示识别框",
  "Start Cloud Game": "启动云游戏",
  "Update Docker": "更新 Git / Docker",
  "Could not load page": "页面加载失败",
};
const t = (value) => (language === "zh_CN" ? chinese[value] : undefined) ?? translate(String(value ?? ""));
for (const el of document.querySelectorAll("[data-text]")) el.textContent = t(el.dataset.text);
for (const el of document.querySelectorAll("[data-label]"))
  el.setAttribute("aria-label", t(el.dataset.label));
$("workspace-nav").setAttribute("aria-label", t("Navigation"));

const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme() {
  const theme = localStorage.getItem("ok-script-theme") || "Auto";
  document.documentElement.dataset.theme =
    theme === "Dark" || (theme === "Auto" && themeMedia.matches) ? "dark" : "light";
  window.dispatchEvent(new Event("workspace-theme"));
}
applyTheme();
themeMedia.addEventListener("change", applyTheme);

async function request(path, options = {}) {
  const init = { ...options };
  if (init.body != null && typeof init.body === "object" && !(init.body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json", ...init.headers };
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  const result = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : await response.text();
  if (!response.ok) throw new Error(result?.detail || result || response.statusText);
  return result;
}
const get = (path) => request(path);
const post = (path, body) => request(path, { method: "POST", body });
function notify(message) {
  const el = document.createElement("button");
  el.textContent = String(message);
  el.onclick = () => el.remove();
  document.querySelector(".workspace-toasts").append(el);
  setTimeout(() => el.remove(), 6500);
}
function button(label, handler) {
  const el = document.createElement("button");
  el.type = "button";
  el.textContent = label;
  el.onclick = handler;
  return el;
}
// The navigation glyphs follow the Qt shell's tasks, calendar, pulse and settings symbols.
const glyphs = {
  tasks: "M4 6l1.5 1.5L8 4m3 2h9M4 12l1.5 1.5L8 10m3 2h9M4 18l1.5 1.5L8 16m3 2h9",
  schedule: "M4 5h16v15H4zM4 10h16M8 3v4m8-4v4",
  log: "M5 3h14v18H5zM8 8h8m-8 4h8m-8 4h5",
  accounts:
    "M3 20c0-6 12-6 12 0m0-11a4 4 0 0 1 4 4m-2 3c3 0 4 2 4 4M12 8a3.5 3.5 0 1 1-7 0 3.5 3.5 0 1 1 7 0",
  tab: "m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16",
  diagnosis: "M2 12h5l3-8 4 16 3-8h5",
  group: "M4 18h16l-2-4V9a6 6 0 0 0-12 0v5zM10 21h4",
  settings: "M4 6h9m4 0h3M4 12h3m4 0h9M4 18h10m4 0h2M13 3v6M7 9v6m7 0v6",
  script: "m4 16 12-12 4 4L8 20H4zM13 7l4 4",
  templates: "M3 4h18v16H3zM4 17l5-5 4 4 3-3 4 4",
  about: "M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18m0 8v6m0-10v1",
};
function icon(key) {
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg"),
    path = document.createElementNS(ns, "path");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  path.setAttribute("d", glyphs[key] || glyphs.tab);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.65");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}

const pages = new Map(),
  listeners = new Set(),
  groups = [];
let current = "",
  dispose,
  sequence = 0,
  dirty = false,
  save = null,
  destination,
  activeUrl = location.href,
  capture,
  game,
  update,
  actionPending = false;
const api = {
  request,
  get,
  post,
  t,
  language,
  notify,
  navigate,
  applyTheme,
  groups,
  subscribe: (listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  setDirty: (value) => {
    dirty = value;
  },
  registerSave: (fn) => {
    save = fn;
  },
};
function register(nav) {
  const list = [];
  const add = (section, id, label, type = id, extra = {}) =>
    list.push({ section, id, label, type, ...extra });
  if (nav.tasks_page ?? (nav.tasks || nav.triggers)) add("Run", "tasks", "Tasks");
  for (const script of nav.imported_task_pages || [])
    add("Run", "imported:" + script.id, script.name, "imported", { script: script.id });
  if (nav.schedule) add("Run", "schedule", "Schedule");
  if (cloud) add("Run", "log", "Log");
  if (cloud) add("Game", "accounts", "Accounts");
  for (const tab of nav.task_tabs)
    add(tab.position === "bottom" ? "" : "Game", "tab:" + tab.id, tab.name, "tab", { tab });
  for (const group of groups.filter((g) => g.top_level))
    add(
      "Game",
      "group:" + group.name,
      group.name === "Notification" ? "Notifications" : group.name,
      "group",
      { group: group.name },
    );
  if (nav.script) add("Develop", "script", "Script");
  if (nav.templates) add("Develop", "templates", "Templates");
  if (cloud) add("Develop", "diagnosis", "Diagnosis");
  add("", "settings", "Settings");
  add("", "about", "About");
  const navRoot = $("workspace-nav");
  navRoot.replaceChildren();
  pages.clear();
  for (const section of ["Run", "Game", "Develop", ""]) {
    const children = list.filter((p) => p.section === section);
    if (!children.length) continue;
    const block = document.createElement("section");
    block.className = "workspace-nav-group";
    if (section) {
      const h = document.createElement("h2");
      h.textContent = t(section);
      block.append(h);
    }
    for (const page of children) {
      pages.set(page.id, page);
      const label = t(page.label),
        el = button("", () => navigate(page.id));
      el.dataset.page = page.id;
      el.className = "workspace-nav-item";
      el.title = label;
      el.setAttribute("aria-label", label);
      const span = document.createElement("span");
      span.textContent = label;
      el.append(icon(page.type), span);
      block.append(el);
    }
    navRoot.append(block);
  }
  selection();
  if (current && !pages.has(current)) navigate(pages.keys().next().value);
}
function selection() {
  for (const el of $("workspace-nav").querySelectorAll("[data-page]")) {
    if (el.dataset.page === current) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  }
}
function urlPage() {
  try {
    return decodeURIComponent(location.hash.slice(1));
  } catch {
    return "";
  }
}
function pageUrl(id, options = {}) {
  const u = new URL(location.href);
  if (options.file && id === "script") u.searchParams.set("file", options.file);
  else u.searchParams.delete("file");
  if (id !== "tasks" && !id.startsWith("imported:")) u.searchParams.delete("task");
  u.hash = encodeURIComponent(id);
  return u;
}
function closeNav() {
  document.body.classList.remove("nav-open");
  $("nav-toggle").setAttribute(
    "aria-expanded",
    String(!mobile.matches && !document.body.classList.contains("compact")),
  );
}
function navigate(id, options = {}) {
  if (!pages.has(id)) return;
  closeNav();
  if (id === current && !options.file) return;
  if (dirty) {
    destination = { id, options };
    if (!$("unsaved-dialog").open) $("unsaved-dialog").showModal();
    return;
  }
  history.pushState(null, "", pageUrl(id, options));
  show(id);
}
window.addEventListener("popstate", () => {
  const id = pages.has(urlPage()) ? urlPage() : pages.keys().next().value;
  if (!id || location.href === activeUrl) return;
  if (dirty) {
    const file = new URL(location.href).searchParams.get("file");
    history.pushState(null, "", activeUrl);
    navigate(id, { file });
  } else show(id);
});
window.addEventListener("beforeunload", (event) => {
  if (dirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("discard").onclick = () => {
  dirty = false;
  $("unsaved-dialog").close();
  navigate(destination.id, destination.options);
};
$("save").onclick = async () => {
  try {
    if (save && (await save())) {
      dirty = false;
      $("unsaved-dialog").close();
      navigate(destination.id, destination.options);
    }
  } catch (error) {
    notify(error.message);
  }
};
for (const el of document.querySelectorAll("[data-close]")) el.onclick = () => el.closest("dialog").close();
function failure(host, error, retry) {
  const box = document.createElement("div");
  box.className = "workspace-alert";
  box.setAttribute("role", "alert");
  const p = document.createElement("p");
  p.textContent = t("Could not load page") + " · " + error.message;
  box.append(p, button(t("Retry"), retry));
  host.replaceChildren(box);
}
async function show(id) {
  const page = pages.get(id);
  if (!page) return;
  const token = ++sequence;
  dispose?.();
  dispose = null;
  dirty = false;
  save = null;
  current = id;
  activeUrl = location.href;
  selection();
  const host = $("workspace-page");
  host.replaceChildren();
  host.scrollTop = 0;
  host.setAttribute("aria-busy", "true");
  document.title = t(page.label) + " · OK-WW";
  try {
    let mount;
    switch (page.type) {
      case "imported":
      case "tasks": {
        const { mountTasks } = await import("./tasks_v3.js");
        mount = () =>
          mountTasks(host, api, {
            script: page.script,
            title: page.script ? page.label : undefined,
            task: new URLSearchParams(location.search).get("task"),
          });
        break;
      }
      case "accounts": {
        const { mountAccounts } = await import("./accounts_v3.js");
        mount = () => mountAccounts(host, api);
        break;
      }
      case "diagnosis":
      case "log": {
        const { mountTools } = await import("./tools_v3.js");
        mount = () => mountTools(host, api, page.type);
        break;
      }
      default:
        mount = () => mountPanel(host, api, page);
    }
    if (token === sequence) dispose = mount();
  } catch (error) {
    if (token === sequence) failure(host, error, () => show(id));
  } finally {
    if (token === sequence) host.setAttribute("aria-busy", "false");
  }
}

const mobile = matchMedia("(max-width:830px)");
$("nav-toggle").onclick = () => {
  if (mobile.matches) {
    $("nav-toggle").setAttribute("aria-expanded", String(document.body.classList.toggle("nav-open")));
  } else {
    const compact = document.body.classList.toggle("compact");
    localStorage.setItem("okww-compact-nav", String(compact));
    $("nav-toggle").setAttribute("aria-expanded", String(!compact));
  }
};
document.body.classList.toggle("compact", localStorage.getItem("okww-compact-nav") === "true");
mobile.addEventListener("change", closeNav);
closeNav();
new ResizeObserver(() =>
  document.body.style.setProperty("--runbar-height", document.querySelector(".runbar").offsetHeight + "px"),
).observe(document.querySelector(".runbar"));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeNav();
});
$("workspace-page").addEventListener("click", () => {
  if (mobile.matches) closeNav();
});

function drawBar() {
  const status = capture?.status,
    device = capture?.devices.find((d) => d.selected);
  $("connection-text").textContent = t(device?.connected ? "Connected" : "Connection");
  $("connection").querySelector(".signal").classList.toggle("online", !!device?.connected);
  $("connection").title = device?.label || t("No device");
  $("runtime-state").textContent = status?.current_task
    ? t(status.current_task)
    : t(status?.starting ? "Connecting" : status && !status.paused ? "Running" : "Idle");
  $("executor").textContent = t(
    game?.busy ? "Cancel start" : status?.paused ? "Start executor" : "Pause executor",
  );
  const updateBusy = update?.enabled && ["queued", "checking", "deferred", "updating", "rebuilding"].includes(update.status);
  $("executor").disabled = actionPending || !status || status.starting || (cloud && (!game || game.stopping))
    || (cloud && updateBusy && status.paused && !game.running);
  $("cancel-task").hidden = !status?.current_task;
  $("cancel-task").disabled = actionPending;
  if (!cloud) return;
  $("cloud-controls").hidden = false;
  const play = $("okww-cloud-game-control");
  play.textContent = t(
    game?.busy ? "Starting Cloud Game..." : game?.running ? "Stop Cloud Game" : "Start Cloud Game",
  );
  play.disabled = actionPending || !game || game.stopping || (!game.running && (updateBusy || game.task_running));
  play.title = game?.error || game?.state || "";
  const updateButton = $("okww-docker-update");
  updateButton.textContent = t(updateBusy ? "Updating Docker..." : "Update Docker");
  updateButton.disabled =
    actionPending || !update?.enabled || updateBusy || update.task_running || update.game_running;
  updateButton.title = update?.message || update?.status || "";
  const version = $("okww-docker-version");
  const revision = update?.current_revision;
  version.textContent = [update?.current_version, revision?.slice(0, 8)].filter(Boolean).join(" · ");
  version.title = revision || "";
}
let pollPromise;
function poll() {
  if (pollPromise) return pollPromise;
  pollPromise = (async () => {
    const jobs = [
      get("/api/ui/capture")
        .then((data) => {
          capture = data;
          if (data.icon_url && document.querySelector(".brand img").getAttribute("src") !== data.icon_url) {
            document.querySelector(".brand img").src = data.icon_url;
            document.querySelector("link[rel=icon]").href = data.icon_url;
          }
          connectEvents(data.event_session_key);
        })
        .catch(() => {
          capture = null;
        }),
    ];
    if (cloud)
      jobs.push(
        get("/api/cloud-game")
          .then((data) => {
            game = data;
          })
          .catch(() => {
            game = null;
          }),
        get("/api/docker-update")
          .then((data) => {
            update = data;
          })
          .catch(() => {
            update = null;
          }),
      );
    await Promise.all(jobs);
    drawBar();
  })().finally(() => {
    pollPromise = null;
  });
  return pollPromise;
}
async function action(fn) {
  if (actionPending) return;
  actionPending = true;
  drawBar();
  try {
    await fn();
  } catch (error) {
    notify(error.message);
  } finally {
    await pollPromise;
    await poll();
    actionPending = false;
    drawBar();
  }
}
$("executor").onclick = () =>
  action(() => {
    if (cloud && game.busy) return post("/api/cloud-game/stop");
    if (cloud && capture.status.paused && !game.running)
      return post("/api/cloud-game/start?run_executor=true");
    return post("/api/executor/" + (capture.status.paused ? "resume" : "pause"));
  });
$("cancel-task").onclick = () => action(() => post("/api/executor/stop-task"));
$("okww-cloud-game-control").onclick = () =>
  action(() => post("/api/cloud-game/" + (game.running ? "stop" : "start")));
$("okww-docker-update").onclick = () =>
  action(() => request("/api/docker-update", { method: "POST", headers: { "X-OK-WW-Update": "1" } }));

function deviceFields() {
  const host = $("device-fields");
  host.replaceChildren();
  for (const [label, key, url] of [
    ["Choose Window", "devices", "devices"],
    ["Capture Method", "capture_methods", "capture-methods"],
    ["Choose Interaction", "interaction_methods", "interaction-methods"],
  ]) {
    const field = document.createElement("label");
    field.textContent = t(label);
    const select = document.createElement("select"),
      items = capture?.[key] || [];
    if (!items.length) {
      select.add(new Option("—", ""));
      select.disabled = true;
    }
    for (const item of items) select.add(new Option(item.label, item.id, false, item.selected));
    select.onchange = async () => {
      select.disabled = true;
      await action(() => post("/api/" + url + "/select", { id: select.value }));
      deviceFields();
    };
    field.append(select);
    host.append(field);
  }
  const field = document.createElement("label"),
    checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = !!capture?.overlay.boxes;
  checkbox.onchange = async () => {
    await action(() => post("/api/overlay", { name: "boxes", value: checkbox.checked }));
    deviceFields();
  };
  field.append(checkbox, document.createTextNode(t("Enable Boxes")));
  host.append(field);
}
$("connection").onclick = () => {
  deviceFields();
  $("device-dialog").showModal();
};
$("refresh-devices").onclick = async () => {
  await action(() => post("/api/devices/refresh"));
  deviceFields();
};

let socket;
const clientId = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
  b.toString(16).padStart(2, "0"),
).join("");
function connectEvents(key) {
  if (socket || !key) return;
  const url = new URL("/api/events", location.href);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("session_key", key);
  url.searchParams.set("client_id", clientId);
  const channel = new WebSocket(url);
  socket = channel;
  channel.onclose = () => {
    if (socket === channel) socket = null;
  };
  channel.onmessage = (event) => {
    const data = JSON.parse(event.data),
      args = data.args || [];
    if (data.ui) {
      capture = data.ui;
      drawBar();
      if ($("device-dialog").open) deviceFields();
    }
    if (data.event === "task_tab") publishTabEvent({ tab_id: args[0], name: args[1], payload: args[2] });
    if (data.event === "task_list_updated")
      get("/api/navigation")
        .then(register)
        .catch((error) => notify(error.message));
    if (data.event === "notification") {
      let message = t(args[0]);
      for (const [key, value] of Object.entries(args[5] || {}))
        message = message.replaceAll("{" + key + "}", String(value));
      notify(message);
      const enabled = groups
        .find((g) => g.name === "Notification")
        ?.fields.some((f) => f.key === "System Notification" && f.value);
      if (args[3] && enabled && window.Notification?.permission === "granted")
        new Notification(t(args[1] || "OK-WW"), { body: message });
    }
    for (const fn of listeners) fn({ ...data, name: data.event });
  };
}
async function init() {
  try {
    const [navigation, settings] = await Promise.all([get("/api/navigation"), get("/api/settings")]);
    groups.splice(0, groups.length, ...settings);
    register(navigation);
    await show(pages.has(urlPage()) ? urlPage() : pages.keys().next().value);
    post("/api/ui/ready").catch(() => {});
  } catch (error) {
    failure($("workspace-page"), error, init);
  }
}
drawBar();
init();
poll();
setInterval(() => {
  if (!document.hidden) poll();
}, 3000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poll();
});
