import { h, replace } from "../dom.js";
import { icon } from "../icons.js";
import { getFailure, listFailures } from "../api.js";
import { voteButton } from "../vote.js";
import { TARGET_LABEL } from "./catalog.js";

// "#/failures": every failed build, grouped by error signature, in the open.
// "#/f/<id>": one group: the scrubbed error, a log excerpt and the builds it hit.
// Restricted groups only reach sysadmins; the server decides.
const STAGE_HELP = {
  "no-recipe": "No source has a recipe",
  concretize: "Recipe or dependency solve",
  build: "Compile",
  package: "Image assembly",
  test: "GPU smoke test",
  publish: "Signing and push",
};
const day = (iso) => (iso ? iso.slice(0, 10) : "—");
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

function stageBadge(stage) {
  return h("span", { class: "fstage", "data-stage": stage, title: STAGE_HELP[stage] || "" }, stage);
}

function failureItem(f, me) {
  return h("li", { class: "fail", "data-restricted": f.restricted ? "true" : null },
    h("a", { class: "fail-link", href: `#/f/${f.id}` },
      h("div", { class: "fail-top" }, stageBadge(f.stage),
        f.restricted && h("span", { class: "chip chip-warn" }, icon("lock"), "Restricted"),
        f.fixUrl && h("span", { class: "chip fail-fixed" }, icon("check"), "Fix known")),
      h("p", { class: "fail-error mono", text: f.error || "(no error line captured)" }),
      h("div", { class: "fail-meta" },
        h("span", { class: "fail-pkgs", text: f.packages.join(", ") }),
        h("span", { text: plural(f.builds, "build") }),
        h("span", { text: `last seen ${day(f.lastSeen)}` }))),
    voteButton({ subject: "failure", key: f.id, votes: f.votes, voted: f.voted, me, label: `failure ${f.id}` }));
}

export function renderFailures({ me, query = "", setTitle }) {
  setTitle("Failures");
  let stage = "";
  let timer = 0;
  let seq = 0;
  const list = h("ul", { class: "fails", "aria-busy": "true" },
    Array.from({ length: 4 }, () => h("li", { class: "fail fail-skel", "aria-hidden": "true" })));
  const count = h("p", { class: "pkgs-count", "aria-live": "polite" });
  const search = h("input", {
    class: "input search-input", type: "search", placeholder: "Search an error or a package: 'nvcc', 'relion'…",
    value: query, autocomplete: "off", spellcheck: "false", "aria-label": "Search failures",
  });
  const chips = h("div", { class: "filters", role: "group", "aria-label": "Stage" },
    [["", "All stages"], ...Object.keys(STAGE_HELP).map((s) => [s, s])].map(([k, label]) => h("button", {
      class: "filter", type: "button", "aria-pressed": String(k === stage), "data-key": k, title: STAGE_HELP[k] || "",
      on: { click: () => { stage = k; for (const b of chips.children) b.setAttribute("aria-pressed", String(b.dataset.key === k)); load(); } },
    }, label)));

  async function load() {
    const mine = ++seq;
    try {
      const res = await listFailures({ q: search.value.trim(), stage });
      if (mine !== seq) return;
      list.removeAttribute("aria-busy");
      const n = res.failures.length;
      count.textContent = `${plural(n, "failure group")}${search.value.trim() ? ` matching “${search.value.trim()}”` : ""}`;
      list.replaceChildren(...(n ? res.failures.map((f) => failureItem(f, me)) : [h("li", { class: "empty" },
        h("p", { class: "empty-title", text: search.value.trim() || stage ? "No matching failures" : "Nothing has failed yet" }),
        h("p", { text: "Every failed build lands here, grouped with others that broke the same way." }))]));
    } catch (err) {
      if (mine !== seq) return;
      list.removeAttribute("aria-busy");
      list.replaceChildren(h("li", { class: "empty" }, h("p", { class: "empty-title", text: "Could not load failures" }), h("p", { text: err.message })));
    }
  }
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const q = search.value.trim();
      history.replaceState(null, "", q ? `#/failures?q=${encodeURIComponent(q)}` : "#/failures");
      load();
    }, 180);
  });
  load();

  return h("div", { class: "catalog failures" },
    h("section", { class: "hero hero-compact" },
      h("p", { class: "eyebrow", text: "Failure database" }),
      h("h1", {}, "Every failed build, ", h("em", { text: "in the open." })),
      h("p", { text: "Builds that broke the same way are grouped by their first error line. Upvote the ones that block your work: the build team fixes the most-wanted first. Logs are scrubbed of paths, host names and tokens." }),
      h("div", { class: "search" }, icon("search", "icon search-icon"), search)),
    h("div", { class: "pkgs-bar" }, chips, count),
    list);
}

export function renderFailure({ id, me, setTitle }) {
  setTitle(`Failure ${id}`);
  const back = h("a", { class: "back", href: "#/failures" }, icon("right", "icon back-icon"), "Failures");
  const view = h("article", { class: "pkgpage", "aria-busy": "true" }, back, h("div", { class: "pkgpage-skel", "aria-hidden": "true" }));
  getFailure(id).then((f) => {
    view.removeAttribute("aria-busy");
    replace(view, [back,
      h("header", { class: "pkgpage-head" },
        h("div", { class: "pkgpage-title" },
          stageBadge(f.stage),
          h("span", { class: "muted", text: STAGE_HELP[f.stage] }),
          f.restricted && h("span", { class: "chip chip-warn" }, icon("lock"), "Restricted · sysadmins only")),
        h("div", { class: "fail-head" },
          h("h1", { class: "fail-h1 mono", text: f.error || "(no error line captured)" }),
          voteButton({ subject: "failure", key: f.id, votes: f.votes, voted: f.voted, me, label: "this failure" })),
        h("dl", { class: "facts" },
          h("dt", { text: "Packages" }), h("dd", {}, f.packages.map((p) => h("a", { class: "chip chip-tag", href: `#/p/${encodeURIComponent(p)}`, text: p }))),
          h("dt", { text: "Seen" }), h("dd", { text: `${plural(f.builds.length, "build")} · first ${day(f.firstSeen)} · last ${day(f.lastSeen)}` }),
          f.fixUrl && [h("dt", { text: "Fix" }), h("dd", {}, h("a", { href: f.fixUrl, rel: "noopener noreferrer", target: "_blank", text: f.fixUrl.replace(/^https:\/\//, "") }))])),
      h("h2", { class: "section-h", text: "Log excerpt" }),
      h("pre", { class: "log fail-log", tabindex: "0", "aria-label": "Scrubbed log excerpt" }, f.excerpt || "No log was captured."),
      h("p", { class: "muted fail-note", text: "Paths, host names, IP addresses and tokens are replaced before a log reaches this page." }),
      f.repairAttempts.length > 0 && [h("h2", { class: "section-h", text: "Repair attempts" }),
        h("ol", { class: "fail-repairs" }, f.repairAttempts.map((a) => (typeof a === "string"
          ? h("li", { text: a })
          : h("li", {},
            h("code", { text: a.rule }), ` on build ${a.build}, retried as build ${a.retry} · ${day(a.at)}`,
            a.why && h("span", { class: "muted fail-why", text: a.why })))))],
      h("h2", { class: "section-h", text: "Builds" }),
      h("div", { class: "table-wrap" },
        h("table", { class: "tbl" },
          h("thead", {}, h("tr", {}, ["Package", "Version", "Target", "Platform", "Source", "Finished"].map((t) => h("th", { scope: "col", text: t })))),
          h("tbody", {}, f.builds.map((b) => h("tr", { "data-status": b.status },
            h("td", {}, h("a", { href: `#/p/${encodeURIComponent(b.package)}`, text: b.package })),
            h("td", { class: "mono", text: b.version }),
            h("td", { text: TARGET_LABEL[b.target] || b.target }),
            h("td", { class: "mono", text: (b.platform || "").replace("linux/", "") || "—" }),
            h("td", { text: b.source }),
            h("td", { text: day(b.finished) }))))))]);
  }).catch((err) => {
    view.removeAttribute("aria-busy");
    view.replaceChildren(back, h("div", { class: "empty" },
      h("p", { class: "empty-title", text: err.status === 404 ? "No such failure" : "Could not load this failure" }),
      h("p", { text: err.status === 404 ? "It does not exist, or it is restricted." : err.message })));
  });
  return view;
}
