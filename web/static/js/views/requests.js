import { h, replace } from "../dom.js";
import { icon } from "../icons.js";
import { createRequest, getQuota, listRequests, searchIndex } from "../api.js";
import { voteButton } from "../vote.js";
import { SOURCE_LABEL, TARGET_LABEL } from "./catalog.js";

// "#/requests": ask for software, see what others asked for, upvote it.
// The server resolves the source (fixed rule order), queues the builds within the
// weekly quota, and skips releases that are already available or queued.
const STATUS = {
  open: "Open", building: "Building", built: "Available", failed: "Failed", declined: "Declined",
};
const STATUS_TONE = { open: "queued", building: "running", built: "published", failed: "failed", declined: "planned" };
const RULE = {
  1: "you picked the source",
  2: "Python/ML stack with conda-forge CUDA builds on both arches",
  3: "Spack recipe with a CUDA variant",
  4: "EasyBuild CUDA easyconfig",
  5: "only CPU recipes exist, so this is a CUDA port attempt",
};
const day = (iso) => (iso ? iso.slice(0, 10) : "");
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

function quotaMeter(q) {
  if (q.unlimited) return h("p", { class: "rq-quota" }, icon("shield"), "Sysadmin: no build quota.");
  const pct = q.limit ? Math.round((q.left / q.limit) * 100) : 0;
  return h("div", { class: "rq-quota" },
    h("span", { text: `${q.left} of ${plural(q.limit, "build job")} left this week` }),
    h("meter", { class: "rq-meter", min: "0", max: String(q.limit || 1), value: String(q.left), low: String(Math.ceil((q.limit || 1) / 4)), optimum: String(q.limit || 1), "aria-label": `${pct}% of your weekly quota left` }));
}

function sourceChips(r) {
  return h("div", { class: "rq-sources" }, ["spack", "easybuild", "pixi"].map((s) => {
    const e = r.sources[s];
    if (!e) return null;
    return h("span", {
      class: "chip rq-src", "data-source": s, "data-cuda": String(e.cuda),
      title: `${SOURCE_LABEL[s]}: ${e.upstream}${e.cuda ? " (CUDA)" : " (CPU only)"}`,
    }, SOURCE_LABEL[s], e.cuda ? h("span", { class: "rq-cuda", text: "CUDA" }) : null);
  }));
}

function requestForm({ me, onDone }) {
  if (!me) {
    return h("div", { class: "notice" }, icon("user"),
      h("div", {}, h("p", { text: "Sign in with your AD account to request software. Browsing requests and failures needs no account." }),
        h("p", {}, h("button", { class: "btn btn-sm", type: "button", on: { click: () => window.dispatchEvent(new CustomEvent("apparadise:signin")) } }, "Sign in"))));
  }
  let pick = null;
  let seq = 0;
  let timer = 0;
  const quota = h("div", { class: "rq-quota-wrap" });
  const results = h("ul", { class: "rq-results", role: "listbox", "aria-label": "Matching packages" });
  const detail = h("div", { class: "rq-detail" });
  const outcome = h("div", { class: "qb-result", "aria-live": "polite" });
  const search = h("input", {
    class: "input search-input", type: "search", id: "rq-q", autocomplete: "off", spellcheck: "false",
    placeholder: "Find a package in Spack, EasyBuild or conda-forge", "aria-controls": "rq-results",
  });
  results.id = "rq-results";

  const loadQuota = () => getQuota().then((q) => quota.replaceChildren(quotaMeter(q))).catch(() => {});

  function showDetail() {
    if (!pick) { detail.replaceChildren(); return; }
    const res = pick.resolution;
    const version = h("select", { class: "input", id: "rq-ver" },
      h("option", { value: "", text: res.versions[0] ? `Newest (${res.versions[0]})` : "Newest" }),
      res.versions.slice(1).map((v) => h("option", { value: v, text: v })));
    const source = h("select", { class: "input", id: "rq-src" },
      h("option", { value: "", text: `Automatic: ${SOURCE_LABEL[res.source] || "none"}` }),
      Object.keys(pick.sources).filter((s) => s !== res.source).map((s) => h("option", { value: s, text: SOURCE_LABEL[s] })));
    const boxes = ["vr", "rtx", "generic"].map((t) => h("label", { class: "check" },
      h("input", { type: "checkbox", value: t, checked: t !== "generic" }),
      h("span", { text: t === "generic" ? `${TARGET_LABEL[t]} (2 jobs)` : TARGET_LABEL[t] })));
    const btn = h("button", { class: "btn btn-primary", type: "submit", disabled: !res.source }, icon("play"), h("span", { text: "Request build" }));
    const form = h("form", { class: "qb", novalidate: true, on: { submit: async (e) => {
      e.preventDefault();
      const targets = boxes.map((b) => b.firstChild).filter((b) => b.checked).map((b) => b.value);
      if (!targets.length) { outcome.replaceChildren(h("div", { class: "form-error", role: "alert" }, icon("alert"), h("span", { text: "Pick at least one target." }))); return; }
      const body = { name: pick.name, targets };
      if (version.value) body.version = version.value;
      if (source.value) body.source = source.value;
      btn.disabled = true;
      try {
        const r = await createRequest(body);
        replace(outcome, [h("div", { class: "notice" }, icon(r.queued.length ? "check" : "bell"),
          h("div", {},
            h("p", {}, h("strong", { text: r.queued.length ? `${plural(r.queued.length, "build")} queued for ${r.package} ${r.version}` : `Nothing new to build for ${r.package} ${r.version}` }),
              ` · ${SOURCE_LABEL[r.source]}`),
            r.skipped.map((s) => h("p", { class: "muted", text: `${TARGET_LABEL[s.target]} (${s.platform.replace("linux/", "")}): ${s.reason}` }))))]);
        loadQuota();
        onDone();
      } catch (err) {
        outcome.replaceChildren(h("div", { class: "form-error", role: "alert" }, icon("alert"), h("span", { text: err.message })));
      } finally {
        btn.disabled = !res.source;
      }
    } } },
    h("p", { class: "rq-why" }, res.source
      ? [h("strong", { text: `Builds from ${SOURCE_LABEL[res.source]}` }), ` because ${RULE[res.rule] || res.reason}.`]
      : h("strong", { text: "No source has a recipe for this." })),
    h("div", { class: "qb-row" },
      h("div", { class: "field" }, h("label", { for: "rq-ver", text: "Version" }), version),
      h("div", { class: "field" }, h("label", { for: "rq-src", text: "Source" }), source)),
    h("fieldset", { class: "qb-targets" }, h("legend", { class: "label", text: "Targets" }), boxes),
    h("div", { class: "qb-actions" }, btn));
    detail.replaceChildren(h("div", { class: "rq-picked" },
      h("h3", { class: "rq-picked-h" }, h("span", { class: "mono", text: pick.name }), sourceChips(pick)), form));
  }

  function choose(r, el) {
    pick = r;
    for (const li of results.children) li.setAttribute("aria-selected", String(li === el));
    outcome.replaceChildren();
    showDetail();
  }

  async function find() {
    const q = search.value.trim();
    const mine = ++seq;
    if (q.length < 2) { results.replaceChildren(); return; }
    try {
      const { results: rows } = await searchIndex(q);
      if (mine !== seq) return;
      results.replaceChildren(...(rows.length ? rows.map((r) => {
        const li = h("li", {
          class: "rq-hit", role: "option", tabindex: "0", "aria-selected": String(pick?.name === r.name),
          on: {
            click: () => choose(r, li),
            keydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(r, li); } },
          },
        },
        h("span", { class: "rq-hit-name mono", text: r.name }),
        sourceChips(r),
        h("span", { class: "rq-hit-pick", text: r.resolution.source ? `builds from ${SOURCE_LABEL[r.resolution.source]}` : "no recipe" }));
        return li;
      }) : [h("li", { class: "rq-none", text: `Nothing called “${q}” in Spack, EasyBuild or conda-forge.` })]));
    } catch (err) {
      if (mine === seq) results.replaceChildren(h("li", { class: "form-error", text: err.message }));
    }
  }
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(find, 200); });
  loadQuota();
  return h("div", { class: "rq-form" },
    h("div", { class: "rq-form-head" }, h("h2", { class: "section-h", text: "Request a package" }), quota),
    h("div", { class: "search" }, icon("search", "icon search-icon"), search),
    results, detail, outcome);
}

function requestRow(r, me) {
  const tone = STATUS_TONE[r.status] || "planned";
  return h("li", { class: "rq", "data-status": r.status },
    h("div", { class: "rq-main" },
      h("div", { class: "rq-top" },
        h("a", { class: "rq-name", href: `#/p/${encodeURIComponent(r.package)}` }, h("span", { class: "mono", text: r.package })),
        h("span", { class: "mono rq-ver", text: r.version }),
        h("span", { class: "status", "data-status": tone, text: STATUS[r.status] || r.status }),
        r.restricted && h("span", { class: "chip chip-warn" }, icon("lock"), "Restricted")),
      h("div", { class: "rq-meta" },
        h("span", { text: r.targets.map((t) => TARGET_LABEL[t] || t).join(" · ") }),
        h("span", { text: `requested ${day(r.created)}${r.requestedBy ? ` by ${r.mine ? "you" : r.requestedBy}` : ""}` }),
        r.failureId && h("a", { href: `#/f/${r.failureId}`, text: "failure record" }))),
    voteButton({ subject: "request", key: r.id, votes: r.votes, voted: r.voted, me, label: `the request for ${r.package}` }));
}

export function renderRequests({ me, setTitle }) {
  setTitle("Requests");
  let mine = false;
  const list = h("ul", { class: "rqs", "aria-busy": "true" });
  const count = h("p", { class: "pkgs-count", "aria-live": "polite" });
  const tabs = h("div", { class: "filters", role: "group", "aria-label": "Show" },
    [[false, "All requests"], [true, "Mine"]].filter(([m]) => !m || me).map(([m, label]) => h("button", {
      class: "filter", type: "button", "aria-pressed": String(m === mine), "data-mine": String(m),
      on: { click: () => { mine = m; for (const b of tabs.children) b.setAttribute("aria-pressed", String(b.dataset.mine === String(m))); load(); } },
    }, label)));

  async function load() {
    try {
      const { requests } = await listRequests(mine);
      list.removeAttribute("aria-busy");
      count.textContent = plural(requests.length, "request");
      list.replaceChildren(...(requests.length ? requests.map((r) => requestRow(r, me)) : [h("li", { class: "empty" },
        h("p", { class: "empty-title", text: mine ? "You have not requested anything yet" : "No requests yet" }),
        h("p", { text: "Search above for a package; the resolver picks the source." }))]));
    } catch (err) {
      list.removeAttribute("aria-busy");
      list.replaceChildren(h("li", { class: "empty" }, h("p", { class: "empty-title", text: "Could not load requests" }), h("p", { text: err.message })));
    }
  }
  load();

  return h("div", { class: "catalog requests" },
    h("section", { class: "hero hero-compact" },
      h("p", { class: "eyebrow", text: "Requests" }),
      h("h1", {}, "Ask for software. ", h("em", { text: "It gets built for every GPU." })),
      h("p", { text: "The resolver picks one source per image: conda-forge for Python/ML stacks, then Spack, then EasyBuild. Builds already available or queued cost nothing, so upvote an existing request instead of filing a new one." })),
    h("section", { class: "apanel" }, requestForm({ me, onDone: load })),
    h("div", { class: "pkgs-bar" }, tabs, count),
    list);
}
