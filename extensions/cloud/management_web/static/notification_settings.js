import { React, ConfigControl } from "./app.js";

const h = React.createElement;
const chinese = {
  "Configured": "已配置",
  "Not configured": "未配置",
  "Leave blank to keep the current value": "留空保留当前值",
  "Clear credential": "清除凭据",
  "Allow browser notifications": "允许浏览器通知",
  "Browser notifications are blocked in site settings.": "浏览器已阻止通知，可在网站权限中更改。",
  "This browser cannot show notifications here.": "当前页面无法使用浏览器通知。",
  "Show notifications in this browser": "在此浏览器中显示通知",
  "Reset all notification settings and clear saved credentials?": "重置全部通知设置并清除已保存的凭据？",
};

function SecretControl({ field, disabled, update, t }) {
  const [draft, setDraft] = React.useState("");
  async function commit(value) {
    if (await update(value)) setDraft("");
  }
  return h("form", {
    className: "notification-credential",
    onSubmit: (event) => {
      event.preventDefault();
      if (!disabled && draft.trim()) commit(draft);
    },
  },
    h("small", { role: "status" }, t(field.configured ? "Configured" : "Not configured")),
    h("input", {
      type: "password", autoComplete: "new-password", disabled, value: draft,
      "aria-label": t(field.key), placeholder: t("Leave blank to keep the current value"),
      onChange: (event) => setDraft(event.target.value),
    }),
    h("button", { type: "submit", disabled: disabled || !draft.trim() }, t("Save")),
    field.configured && h("button", {
      type: "button", disabled, onClick: () => commit({ clear: true }),
    }, t("Clear credential")),
  );
}

function BrowserPermission({ t, notify }) {
  const [permission, setPermission] = React.useState(() => window.Notification?.permission);
  const [requesting, setRequesting] = React.useState(false);
  React.useEffect(() => {
    const refresh = () => setPermission(window.Notification?.permission);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);
  if (permission === "granted") return null;
  if (permission !== "default") {
    return h("small", null, t(permission === "denied"
      ? "Browser notifications are blocked in site settings."
      : "This browser cannot show notifications here."));
  }
  return h("button", {
    type: "button", disabled: requesting, onClick: async () => {
      setRequesting(true);
      try {
        setPermission(await window.Notification.requestPermission());
      } catch (error) {
        notify(error.message);
      } finally {
        setRequesting(false);
      }
    },
  }, t("Allow browser notifications"));
}

export function NotificationSettings({ api, group, pending, onUpdate, onReset }) {
  const t = (key) => (api.language === "zh_CN" ? chinese[key] : undefined) ?? api.t(key);
  const disabled = pending !== null;
  const rows = group.fields.map((field) => {
    const system = field.key === "System Notification";
    const update = (value) => onUpdate(group, field, value);
    return h("div", { key: field.key, className: "task-config-row" + (field.sub_config ? " sub-config" : "") },
      h("span", null, h("strong", null, t(field.key)),
        h("small", null, t(system ? "Show notifications in this browser" : field.description))),
      field.kind === "secret" ? h(SecretControl, { field, disabled, update, t })
        : h(ConfigControl, { field, disabled, onCommit: (value) => {
          if (JSON.stringify(value) !== JSON.stringify(field.value)) update(value);
        } }),
      system && field.value && h(BrowserPermission, { t, notify: api.notify }),
    );
  });
  return h("section", { className: "settings-page top-level-settings-page", "aria-label": t(group.name) },
    h("h1", null, t(group.name)),
    h("p", { className: "settings-description" }, t(group.description)),
    h("div", { className: "surface-card top-level-settings-card" },
      h("div", { className: "task-config" }, rows,
        h("footer", null, h("button", { type: "button", disabled, onClick: () => {
          if (window.confirm(t("Reset all notification settings and clear saved credentials?"))) onReset(group);
        } }, t("Reset Config"))))),
  );
}
