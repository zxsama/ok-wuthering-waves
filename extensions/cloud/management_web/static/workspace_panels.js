import {
  React,
  ReactDOM,
  FluentProvider,
  accentTheme,
  lightTheme,
  darkTheme,
  TaskTab,
  Scripts,
  Templates,
  Schedule,
  SettingsGroup,
  About,
} from "./app.js";
import { WorkspaceSettings } from "./workspace_settings.js";
import { NotificationSettings } from "./notification_settings.js";

const h = React.createElement;

export function mountPanel(host, api, page) {
  const root = ReactDOM.createRoot(host);
  host.classList.add("workspace-panel");
  let mounted = true;
  let pending = null;
  let about = null;
  const update = async (group, field, value, section) => {
    if (pending) return;
    pending = group.name;
    render();
    try {
      if (
        group.name === "Notification" &&
        field?.key === "System Notification" &&
        value &&
        window.Notification?.permission === "default"
      ) {
        try {
          await Notification.requestPermission();
        } catch (error) {
          api.notify(error.message);
        }
      }
      const url = "/api/settings/" + encodeURIComponent(group.name);
      const saved = field
        ? await api.post(url + "/config", { key: field.key, value })
        : await api.post(url + "/reset" + (section ? "?section=" + encodeURIComponent(section) : ""));
      const index = api.groups.findIndex((item) => item.name === saved.name);
      if (index >= 0) api.groups[index] = saved;
      return true;
    } catch (error) {
      api.notify(error.message);
    } finally {
      pending = null;
      if (mounted) render();
    }
  };
  const editable = { notify: api.notify, onDirtyChange: api.setDirty, registerSave: api.registerSave };
  function render() {
    const dark = document.documentElement.dataset.theme === "dark";
    const groupProps = { loading: false, pending, onUpdate: update, onReset: (group) => update(group) };
    let panel;
    switch (page.type) {
      case "tab":
        panel = h(TaskTab, { ...editable, tab: page.tab });
        break;
      case "script":
        panel = h(Scripts, { ...editable, initialScript: new URLSearchParams(location.search).get("file") });
        break;
      case "templates":
        panel = h(Templates, { notify: api.notify });
        break;
      case "schedule":
        panel = h(Schedule, { notify: api.notify });
        break;
      case "group": {
        const group = api.groups.find((g) => g.name === page.group);
        panel =
          group?.name === "Notification"
            ? h(NotificationSettings, { ...groupProps, api, group })
            : h(SettingsGroup, { ...groupProps, group });
        break;
      }
      case "settings":
        panel = h(WorkspaceSettings, { api, update, pending });
        break;
      case "about":
        panel = h(About, {
          info: about,
          updateResult: null,
          updateChecking: false,
          updateError: null,
          notify: api.notify,
        });
        break;
    }
    root.render(
      h(
        FluentProvider,
        {
          theme: accentTheme(
            dark ? darkTheme : lightTheme,
            dark,
            getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
          ),
        },
        panel,
      ),
    );
  }
  if (page.type === "about") {
    api
      .get("/api/about")
      .then((result) => {
        if (mounted) {
          about = result;
          render();
        }
      })
      .catch((error) => api.notify(error.message));
  }
  render();
  window.addEventListener("workspace-theme", render);
  return () => {
    mounted = false;
    window.removeEventListener("workspace-theme", render);
    root.unmount();
    host.classList.remove("workspace-panel");
  };
}
