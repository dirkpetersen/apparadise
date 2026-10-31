import { h } from "../dom.js";
import { icon } from "../icons.js";
import { createBuilderToken, disableBuilder, getBuildLog, listBuilders, listBuilds, queueBuild } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmDialog } from "../components/confirm-dialog.js";
import { STATUS_LABEL, TARGET_LABEL } from "./catalog.js";

// Sysadmin console: queue builds, watch the queue, manage builders.
const STATUS_FILTERS = [["", "All"], ["queued", "Queued"], ["running", "Running"], ["failed", "Failed"], ["published", "Published"]];
const REFRESH_MS = 10000;

function time(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

function queueForm(onQueued) {
  const pkg = h("input", { class: "input", id: "qb-pkg", required: true, placeholder: "gromacs", autocomplete: "off", spellcheck: "false" });
  const ver = h("input", { class: "input", id: "qb-ver", required: true, placeholder: "2026.3", autocomplete: "off", spellcheck: "false" });
  const source = h("select", { class: "input", id: "qb-src" },
    h("option", { value: "", text: "Automatic (resolver)" }),
    h("option", { value: "spack", text: "Spack" }), h("option", { value: "easybuild", text: "EasyBuild" }),
    h("option", { value: "pixi", text: "pixi · conda-forge" }));
  const extra = h("input", { class: "input", id: "qb-extra", placeholder: "Spack spec or easyconfig file (optional)", autocomplete: "off", spellcheck: "false" });
  const boxes = ["rtx", "vr", "generic"].map((t) => h("label", { class: "check" },
    h("input", { type: "checkbox", value: t, checked: t === "rtx" }), h("span", { text: TARGET_LABEL[t] })));
  const result = h("div", { class: "qb-result", "aria-live": "polite" });
  const btn = h("button", { class: "btn btn-primary", type: "submit" }, icon("play"), h("span", { text: "Queue build" }));
  const form = h("form", { class: "qb", novalidate: true, on: { submit: async (e) => {
    e.preventDefault();
    const targets = boxes.map((b) => b.firstChild).filter((b) => b.checked).map((b) => b.value);
    if (!pkg.value.trim() || !ver.value.trim() || !targets.length) {
      toast.error("Enter a package, a version and at least one target.");
      return;
    }
    const body = { package: pkg.value.trim().toLowerCase(), version: ver.value.trim(), targets };
    if (source.value) body.source = source.value;
    const x = extra.value.trim();
    if (x) body[x.endsWith(".eb") ? "easyconfig" : "spec"] = x;
    btn.disabled = true;
    try {
      const r = await queueBuild(body);
      result.replaceChildren(h("div", { class: "notice" }, icon("check"),
        h("div", {},
          h("p", {}, h("strong", { text: `${r.queued.length} build${r.queued.length === 1 ? "" : "s"} queued` }),
            ` · ${r.source}, rule ${r.rule}: ${r.reason}${r.cudaPort ? " (CUDA port attempt)" : ""}`),
          r.skipped.map((s) => h("p", { class: "muted", text: `Skipped ${s.target} (${s.platform}): ${s.reason}` })))));
      onQueued();
    } catch (err) {
      result.replaceChildren(h("div", { class: "form-error", role: "alert" }, icon("alert"), h("span", { text: err.message })));
    } finally {
      btn.disabled = false;
    }
  } } },
  h("div", { class: "qb-row" },
    h("div", { class: "field" }, h("label", { for: "qb-pkg", text: "Package" }), pkg),
    h("div", { class: "field" }, h("label", { for: "qb-ver", text: "Version" }), ver),
    h("div", { class: "field" }, h("label", { for: "qb-src", text: "Source" }), source)),
  h("div", { class: "field" }, h("label", { for: "qb-extra", text: "Recipe detail" }), extra),
  h("fieldset", { class: "qb-targets" }, h("legend", { class: "label", text: "Targets" }), boxes),
  h("div", { class: "qb-actions" }, btn),
  result);
  return form;
}

function buildsPanel() {
  let filter = "";
  let open = null; // build id whose log is shown
  const body = h("tbody");
  const logBox = h("div", { class: "logbox" });
  const chips = h("div", { class: "filters", role: "group", "aria-label": "Status" },
    STATUS_FILTERS.map(([k, label]) => h("button", { class: "filter", type: "button", "data-key": k,
      "aria-pressed": String(k === filter), on: { click: () => {
        filter = k;
        for (const b of chips.children) b.setAttribute("aria-pressed", String(b.dataset.key === k));
        load();
      } } }, label)));

  async function showLog(id) {
    open = id;
    logBox.replaceChildren(h("p", { class: "muted", text: `Loading the log of build ${id}…` }));
    try {
      const { log, size } = await getBuildLog(id);
      const pre = h("pre", { class: "log", tabindex: "0", text: log || "(empty)" });
      logBox.replaceChildren(h("div", { class: "log-head" },
        h("strong", { text: `Build ${id}` }), h("span", { class: "muted", text: ` · ${size} bytes, last 64 KiB` }),
        h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close log",
          on: { click: () => { open = null; logBox.replaceChildren(); } } }, icon("x"))), pre);
      pre.scrollTop = pre.scrollHeight;
    } catch (err) {
      logBox.replaceChildren(h("p", { class: "form-error", text: err.message }));
    }
  }

  async function load() {
    try {
      const { builds } = await listBuilds(filter);
      body.replaceChildren(...(builds.length ? builds.map((b) => h("tr", { "data-status": b.status },
        h("td", { class: "mono", text: String(b.id) }),
        h("td", {}, h("a", { href: `#/p/${encodeURIComponent(b.package)}`, text: b.package }), " ",
          h("span", { class: "mono muted", text: b.version }),
          b.restricted ? h("span", { class: "chip chip-warn", title: "Restricted" }, icon("lock")) : null),
        h("td", { class: "mono", text: `${b.target} · ${b.platform.replace("linux/", "")}` }),
        h("td", { text: b.source }),
        h("td", {}, h("span", { class: "status", "data-status": b.status, text: STATUS_LABEL[b.status] || b.status }),
          // The stage matters while running (where it is) and on failure (where it broke).
          b.stage && (b.status === "running" || b.status === "failed")
            ? h("span", { class: "muted", text: ` ${b.status === "failed" ? "at " : ""}${b.stage}` }) : null),
        h("td", { class: "muted", text: b.builder || "—" }),
        h("td", { class: "muted", text: time(b.finished_at || b.started_at || b.created_at) }),
        h("td", {}, b.hasLog ? h("button", { class: "btn btn-ghost", type: "button", on: { click: () => showLog(b.id) } }, "Log") : null)))
        : [h("tr", {}, h("td", { colspan: "8", class: "muted", text: "No builds yet." }))]));
      if (open) showLog(open);
    } catch (err) {
      body.replaceChildren(h("tr", {}, h("td", { colspan: "8", class: "form-error", text: err.message })));
    }
  }

  const panel = h("section", { class: "apanel", "aria-labelledby": "ab-h" },
    h("div", { class: "apanel-head" }, h("h2", { id: "ab-h", class: "section-h", text: "Builds" }), chips),
    h("div", { class: "table-wrap" }, h("table", { class: "tbl" },
      h("thead", {}, h("tr", {}, ["#", "Package", "Target", "Source", "Status", "Builder", "Updated", ""].map((t) => h("th", { scope: "col", text: t })))),
      body)),
    logBox);
  return { panel, load };
}

function buildersPanel() {
  const list = h("ul", { class: "builders" });
  const tokenBox = h("div", {});
  async function load() {
    try {
      const { builders } = await listBuilders();
      list.replaceChildren(...(builders.length ? builders.map((b) => h("li", { class: "builder", "data-disabled": String(!!b.disabled) },
        h("div", {}, h("strong", { text: b.name }), h("span", { class: "muted mono", text: ` ${b.platform}` }),
          h("span", { class: "chip", text: b.onprem ? "on-prem" : "cloud" })),
        h("span", { class: "muted", text: b.disabled ? "Disabled" : b.last_seen ? `Seen ${time(b.last_seen)}` : "Never seen" }),
        !b.disabled && h("button", { class: "btn btn-ghost", type: "button", on: { click: async () => {
          if (!(await confirmDialog({ title: `Disable ${b.name}?`, message: "It can no longer claim jobs. Re-register it with a new token to bring it back.", confirmLabel: "Disable", danger: true }))) return;
          try { await disableBuilder(b.id); toast.show(`${b.name} disabled`); load(); } catch (err) { toast.error(err.message); }
        } } }, "Disable"))) : [h("li", { class: "muted", text: "No builders registered yet." })]));
    } catch (err) {
      list.replaceChildren(h("li", { class: "form-error", text: err.message }));
    }
  }
  const newToken = h("button", { class: "btn", type: "button", on: { click: async () => {
    try {
      const { token, expiresAt } = await createBuilderToken();
      const panel = h("code-panel");
      panel.tabs = [{ label: "builder/.env", content: `REGISTRATION_TOKEN=${token}` }];
      panel.hint = `One-time token, valid until ${time(expiresAt)}. It is shown only now.`;
      tokenBox.replaceChildren(panel);
    } catch (err) { toast.error(err.message); }
  } } }, icon("key"), h("span", { text: "New registration token" }));
  const panel = h("section", { class: "apanel", "aria-labelledby": "aw-h" },
    h("div", { class: "apanel-head" }, h("h2", { id: "aw-h", class: "section-h", text: "Builders" }), newToken),
    tokenBox, list);
  return { panel, load };
}

export function renderAdmin({ me, setTitle }) {
  setTitle("Admin");
  if (!me?.isSysadmin) {
    return h("div", { class: "empty" }, h("p", { class: "empty-title", text: "Sysadmins only" }),
      h("p", { text: "This page is for members of RCO-Admins." }));
  }
  const builds = buildsPanel();
  const builders = buildersPanel();
  builds.load();
  builders.load();
  const timer = setInterval(() => {
    if (!view.isConnected) { clearInterval(timer); return; }
    if (document.visibilityState === "visible") builds.load();
  }, REFRESH_MS);
  const view = h("div", { class: "admin" },
    h("section", { class: "hero" },
      h("p", { class: "eyebrow", text: "Sysadmin" }),
      h("h1", { text: "Build console" }),
      h("p", { text: "Queue builds through the resolver, watch the pipeline, and manage the pull-based builders." })),
    h("section", { class: "apanel", "aria-labelledby": "aq-h" },
      h("h2", { id: "aq-h", class: "section-h", text: "Queue a build" }), queueForm(() => builds.load())),
    builds.panel,
    builders.panel);
  return view;
}
