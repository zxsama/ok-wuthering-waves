import { React, Settings, ConfigControl } from "./app.js";

const h = React.createElement;

// Qt gives each named config section an anchor and keeps dependent fields with their parent.
export function WorkspaceSettings({ api, pending, update }) {
  const sections = [];
  for (const group of api.groups.filter((group) => !group.top_level)) {
    const buckets = new Map();
    for (const field of group.fields) {
      const name = field.section || group.name;
      if (!buckets.has(name)) buckets.set(name, []);
      buckets.get(name).push(field);
    }
    if (buckets.has("Other")) {
      const other = buckets.get("Other");
      buckets.delete("Other");
      buckets.set("Other", other);
    }
    for (const [name, fields] of buckets) sections.push({ name, fields, group });
  }
  const [active, setActive] = React.useState(0);
  const column = React.useRef(null);
  const anchors = [api.t("App Config"), ...sections.map((s) => api.t(s.name))];
  React.useEffect(() => {
    const scroll = column.current?.closest("main");
    if (!scroll) return;
    const sync = () => {
      let index = 0;
      const edge = scroll.getBoundingClientRect().top + 32;
      column.current.querySelectorAll("[data-anchor]").forEach((section, i) => {
        if (section.getBoundingClientRect().top <= edge) index = i;
      });
      setActive(index);
    };
    scroll.addEventListener("scroll", sync, { passive: true });
    return () => scroll.removeEventListener("scroll", sync);
  }, []);
  const jump = (index) => {
    column.current.querySelectorAll("[data-anchor]")[index]?.scrollIntoView({ block: "start" });
    setActive(index);
  };
  const appearance = h(Settings, {
    groups: [],
    loading: false,
    pending,
    theme: localStorage.getItem("ok-script-theme") || "Auto",
    language: localStorage.getItem("ok-script-language") || "Auto",
    onTheme: (choice) => {
      localStorage.setItem("ok-script-theme", choice);
      api.applyTheme();
    },
    onLanguage: (choice) => {
      localStorage.setItem("ok-script-language", choice);
      location.reload();
    },
  });
  return h(
    "section",
    { className: "workspace-settings" },
    h("h1", null, api.t("Settings")),
    h(
      "div",
      { className: "workspace-settings-layout" },
      h(
        "div",
        { className: "settings-jump" },
        anchors.map((label, i) =>
          h(
            "button",
            {
              type: "button",
              key: i,
              "aria-current": active === i ? "location" : undefined,
              onClick: () => jump(i),
            },
            label,
          ),
        ),
      ),
      h(
        "div",
        { className: "workspace-settings-content", ref: column },
        h("div", { "data-anchor": "appearance", className: "workspace-appearance" }, appearance),
        sections.map(({ name, group, fields }, i) =>
          h(
            "section",
            {
              className: "settings-group",
              "data-anchor": i,
              key: group.name + "/" + name,
            },
            h("h2", null, api.t(name)),
            h(
              "div",
              { className: "surface-card task-config" },
              fields.map((field) =>
                h(
                  "div",
                  {
                    className: "task-config-row" + (field.sub_config ? " sub-config" : ""),
                    key: field.key,
                  },
                  h(
                    "span",
                    null,
                    h("strong", null, api.t(field.key)),
                    field.description && h("small", null, api.t(field.description)),
                  ),
                  h(ConfigControl, {
                    field,
                    disabled: pending !== null,
                    onCommit: (value) => update(group, field, value),
                  }),
                ),
              ),
            ),
            h(
              "button",
              {
                type: "button",
                className: "settings-reset",
                disabled: pending !== null,
                onClick: () => update(group, null, undefined, fields[0]?.section),
              },
              api.t("Reset Config"),
            ),
          ),
        ),
      ),
    ),
  );
}
