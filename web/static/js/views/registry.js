import { h } from "../dom.js";
import { icon } from "../icons.js";
import { getRegistry, getRepository } from "../api.js";
import "../components/code-panel.js";

// "#/registry": what Harbor actually holds (the catalog says what should exist).
// "#/registry/<project>/<repo>": one repository's artifacts, with pull commands.
// The server decides which projects this user may see (restricted and cache: sysadmins).
const PROJECT = {
  public: { label: "Public", note: "Every signed image the pipeline published. Anyone on the cluster can pull these." },
  restricted: { label: "Restricted", note: "Licence-gated images. Visible and pullable by RCO-Admins only." },
  cache: { label: "Cache", note: "The NGC proxy cache (base images) plus test tags. Sysadmins only." },
};

const day = (iso) => (iso ? iso.slice(0, 10) : "—");
const shortDigest = (d) => `${d.slice(0, 19)}…`;
function size(bytes) {
  if (!bytes) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
  return `${(bytes / 1000 ** i).toFixed(i >= 3 ? 1 : 0)} ${u[i]}`;
}
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const repoHref = (project, name) =>
  `#/registry/${encodeURIComponent(project)}/${name.split("/").map(encodeURIComponent).join("/")}`;

function failure(err, what) {
  return h("div", { class: "empty" },
    h("p", { class: "empty-title", text: err.status === 404 ? `No such ${what}` : err.status === 503 ? "Registry unavailable" : `Could not load the ${what}` }),
    h("p", { text: err.message }));
}

function repoRow(project, r) {
  return h("li", { class: "repo" },
    h("a", { class: "repo-link", href: repoHref(project, r.name) },
      h("span", { class: "repo-name mono", text: r.name }),
      h("span", { class: "repo-stats" },
        h("span", { text: plural(r.artifacts, "image") }),
        h("span", { text: plural(r.pulls, "pull") }),
        h("span", { text: `updated ${day(r.updated)}` })),
      icon("right", "icon repo-go")));
}

export function renderRegistry({ setTitle }) {
  setTitle("Registry");
  const body = h("div", { class: "reg-body", "aria-busy": "true" }, h("div", { class: "pkgpage-skel", "aria-hidden": "true" }));
  const host = h("p", { class: "reg-host" });
  getRegistry().then((res) => {
    body.removeAttribute("aria-busy");
    host.replaceChildren(icon("bucket"), h("span", { class: "mono", text: res.host }));
    if (!res.configured) {
      body.replaceChildren(h("div", { class: "notice" }, icon("bell"),
        h("p", { text: "This server has no Harbor connection (HARBOR_URL is empty), so there is nothing to browse yet." })));
      return;
    }
    body.replaceChildren(...res.projects.map((p) => h("section", { class: "reg-project", "data-project": p.name },
      h("div", { class: "reg-project-head" },
        h("h2", { class: "section-h" }, PROJECT[p.name]?.label || p.name,
          p.name !== "public" && h("span", { class: "chip chip-warn" }, icon("lock"), "Sysadmins")),
        h("p", { class: "muted", text: PROJECT[p.name]?.note || "" })),
      p.repositories.length
        ? h("ul", { class: "repos" }, p.repositories.map((r) => repoRow(p.name, r)))
        : h("p", { class: "reg-none", text: "No images yet." }))));
  }).catch((err) => { body.removeAttribute("aria-busy"); body.replaceChildren(failure(err, "registry")); });

  return h("div", { class: "registry" },
    h("section", { class: "hero hero-compact" },
      h("p", { class: "eyebrow", text: "Container registry" }),
      h("h1", {}, "Every image, ", h("em", { text: "exactly as Harbor holds it." })),
      h("p", { text: "The registry is the source of truth. Modules and the staged squashfs files on CephFS are rebuilt from these images, and every pipeline image is signed with cosign." }),
      host),
    body);
}

function commandPanel(repo, a) {
  const panel = h("code-panel");
  const c = a.commands;
  panel.tabs = [
    { label: "Slurm", content: `# Pyxis pulls from Harbor and starts the image unprivileged\n${c.srun}` },
    { label: "Enroot", content: c.enroot },
    { label: "Podman", content: c.podman },
    { label: "By digest", content: `${repo.host}/${repo.project}/${repo.name}@${a.digest}` },
  ];
  panel.hint = a.tags.length
    ? `Tag ${a.tags[0]}. Use the digest when you need an image that can never change under you.`
    : "This image has no tag, so the commands use its digest.";
  return panel;
}

function artifactRow(a, selected, select) {
  const label = a.tags[0] || shortDigest(a.digest);
  return h("tr", { "aria-selected": String(selected) },
    h("td", {}, a.tags.length ? a.tags.map((t) => h("code", { class: "reg-tag", text: t })) : h("span", { class: "muted", text: "untagged" })),
    h("td", {}, a.platforms.map((p) => h("span", { class: "chip reg-plat", text: p.replace("linux/", "") })),
      a.index && h("span", { class: "muted reg-index", text: "multi-arch" })),
    h("td", { class: "mono", title: a.digest, text: shortDigest(a.digest) }),
    h("td", { class: "num", text: size(a.size) }),
    h("td", { text: day(a.pushed) }),
    h("td", {}, a.signed
      ? h("span", { class: "reg-signed" }, icon("shield"), "Signed")
      : h("span", { class: "muted", text: "Unsigned" })),
    h("td", {}, h("button", {
      class: "btn btn-ghost", type: "button", "aria-pressed": String(selected),
      "aria-label": `Show pull commands for ${label}`, on: { click: select },
    }, "Use")));
}

export function renderRepository({ project, name, setTitle }) {
  setTitle(`${project}/${name}`);
  const back = h("a", { class: "back", href: "#/registry" }, icon("right", "icon back-icon"), "Registry");
  const view = h("article", { class: "pkgpage", "aria-busy": "true" }, back, h("div", { class: "pkgpage-skel", "aria-hidden": "true" }));
  getRepository(project, name).then((repo) => {
    view.removeAttribute("aria-busy");
    let sel = repo.artifacts[0] || null;
    const cmd = h("div", { class: "cmds" });
    const rows = h("tbody");
    function paint() {
      cmd.replaceChildren(sel
        ? commandPanel(repo, sel)
        : h("div", { class: "notice" }, icon("bell"), h("p", { text: "This repository has no images." })));
      rows.replaceChildren(...repo.artifacts.map((a) => artifactRow(a, a === sel, () => { sel = a; paint(); })));
    }
    paint();
    view.replaceChildren(back,
      h("header", { class: "pkgpage-head" },
        h("div", { class: "pkgpage-title" },
          h("h1", { class: "reg-title" }, h("span", { class: "reg-project-name", text: `${repo.project}/` }), repo.name),
          repo.project !== "public" && h("span", { class: "chip chip-warn" }, icon("lock"), "Sysadmins only")),
        h("p", { class: "lede" }, h("span", { class: "mono", text: `${repo.host}/${repo.project}/${repo.name}` }),
          repo.package && [" · ", h("a", { href: `#/p/${encodeURIComponent(repo.package)}`, text: "catalog page" })])),
      h("h2", { class: "section-h", text: "Pull" }),
      cmd,
      h("h2", { class: "section-h", text: plural(repo.artifacts.length, "image") }),
      h("div", { class: "table-wrap" },
        h("table", { class: "tbl reg-tbl" },
          h("thead", {}, h("tr", {}, ["Tags", "Platforms", "Digest", "Size", "Pushed", "Signature", ""].map((t) =>
            h("th", { scope: "col" }, t || h("span", { class: "visually-hidden", text: "Action" }))))),
          rows)));
  }).catch((err) => { view.removeAttribute("aria-busy"); view.replaceChildren(back, failure(err, "repository")); });
  return view;
}
