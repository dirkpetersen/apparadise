import { h } from "../dom.js";
import { icon } from "../icons.js";
import { listCatalog } from "../api.js";

export const SOURCE_LABEL = { spack: "Spack", easybuild: "EasyBuild", pixi: "pixi · conda-forge" };
export const TARGET_LABEL = { vr: "Vera Rubin", rtx: "RTX farm", generic: "Generic" };
export const STATUS_LABEL = {
  published: "Available", running: "Building", queued: "Queued", failed: "Failed", planned: "Planned",
};
const FILTERS = [["all", "All"], ["available", "Available"], ["spack", "Spack"], ["easybuild", "EasyBuild"], ["pixi", "pixi"]];

// One dot per target, coloured by status: the shape of a package at a glance.
export function targetDots(targets, all = ["vr", "rtx", "generic"]) {
  return h("ul", { class: "tdots", "aria-label": "Targets" },
    all.filter((t) => t !== "generic" || targets[t]).map((t) => {
      const s = targets[t] || "none";
      return h("li", { class: "tdot", "data-status": s, title: `${TARGET_LABEL[t]}: ${STATUS_LABEL[s] || "not planned"}` },
        h("span", { class: "tdot-mark", "aria-hidden": "true" }),
        h("span", { class: "tdot-name", text: t }),
        h("span", { class: "visually-hidden", text: `: ${STATUS_LABEL[s] || "not planned"}` }));
    }));
}

function card(p) {
  return h("li", { class: "pkg" },
    h("a", { class: "pkg-link", href: `#/p/${encodeURIComponent(p.name)}` },
      h("div", { class: "pkg-top" },
        h("h3", { class: "pkg-title", text: p.title }),
        p.visibility === "restricted" && h("span", { class: "chip chip-warn", title: "Visible to sysadmins only" }, icon("lock"), "Restricted")),
      h("p", { class: "pkg-desc", text: p.description || "No description yet." }),
      h("div", { class: "pkg-meta" },
        h("span", { class: "chip", "data-source": p.source }, SOURCE_LABEL[p.source] || p.source),
        p.latest ? h("span", { class: "pkg-ver", text: p.latest }) : h("span", { class: "pkg-ver pkg-ver-none", text: "version at build time" }),
        targetDots(p.targets))));
}

export function renderCatalog({ query, setTitle }) {
  setTitle("");
  let all = [];
  let filter = "all";
  let timer = 0;
  let seq = 0;
  const list = h("ul", { class: "pkgs", "aria-busy": "true" });
  const count = h("p", { class: "pkgs-count", "aria-live": "polite" });
  const search = h("input", {
    class: "input search-input", type: "search", id: "q", placeholder: "Search 'gromacs', 'cryo-em', 'pytorch'…",
    value: query, autocomplete: "off", spellcheck: "false", "aria-label": "Search the catalog",
  });
  const chips = h("div", { class: "filters", role: "group", "aria-label": "Filter" },
    FILTERS.map(([k, label]) => h("button", {
      class: "filter", type: "button", "aria-pressed": k === filter ? "true" : "false", "data-key": k,
      on: { click: () => { filter = k; for (const b of chips.children) b.setAttribute("aria-pressed", String(b.dataset.key === k)); paint(); } },
    }, label)));

  function visible() {
    return all.filter((p) => filter === "all" || (filter === "available" ? p.published : p.source === filter));
  }
  function paint() {
    const items = visible();
    list.removeAttribute("aria-busy");
    count.textContent = `${items.length} package${items.length === 1 ? "" : "s"}${search.value.trim() ? ` matching “${search.value.trim()}”` : ""}`;
    list.replaceChildren(...(items.length ? items.map(card) : [h("li", { class: "empty" },
      h("p", { class: "empty-title", text: "Nothing here yet" }),
      h("p", { text: "Requests for new software arrive in a later phase. Until then, ask the research computing team." }))]));
  }
  async function load() {
    const mine = ++seq;
    try {
      const res = await listCatalog(search.value.trim());
      if (mine !== seq) return;
      all = res.packages;
      paint();
    } catch (err) {
      if (mine !== seq) return;
      list.removeAttribute("aria-busy");
      list.replaceChildren(h("li", { class: "empty" }, h("p", { class: "empty-title", text: "Could not load the catalog" }), h("p", { text: err.message })));
    }
  }
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const q = search.value.trim();
      history.replaceState(null, "", q ? `#/?q=${encodeURIComponent(q)}` : "#/");
      load();
    }, 180);
  });
  // Skeleton cards while loading.
  list.replaceChildren(...Array.from({ length: 6 }, () => h("li", { class: "pkg pkg-skel", "aria-hidden": "true" })));
  load();

  return h("div", { class: "catalog" },
    h("section", { class: "hero" },
      h("p", { class: "eyebrow", text: "CUDA software catalog" }),
      h("h1", {}, "Built once per GPU. ", h("em", { text: "Run it any way you like." })),
      h("p", { text: "Every package is a signed container image, tuned for the Vera Rubin NVL72 and the RTX farm. Load it as a module, start it with srun, or pick it in Run:ai. No root, no guessing which build system it came from." }),
      h("div", { class: "search" }, icon("search", "icon search-icon"), search)),
    h("div", { class: "pkgs-bar" }, chips, count),
    list);
}
