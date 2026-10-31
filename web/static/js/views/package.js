import { h } from "../dom.js";
import { icon } from "../icons.js";
import { getPackage } from "../api.js";
import { SOURCE_LABEL, STATUS_LABEL, TARGET_LABEL, targetDots } from "./catalog.js";

const TARGET_HW = {
  vr: "NVIDIA Vera (Arm, 88 cores) · Rubin GPU",
  rtx: "x86-64 · RTX PRO 6000 Blackwell (sm_120)",
  generic: "Portable · Volta and newer, x86-64 and Arm",
};
const STAGES = ["resolve", "generate", "build", "package", "test", "publish"];
// Failure stage names map onto the pipeline stage where they happen.
const STAGE_OF = { "no-recipe": "resolve", concretize: "generate", build: "build", package: "package", test: "test", publish: "publish" };

function shortDigest(d) { return d ? `${d.slice(0, 19)}…` : ""; }

function commandPanel(pkg, version, target, info) {
  const c = info.commands;
  const panel = h("code-panel");
  panel.tabs = [
    { label: "Module", content: `${c.module}\n${pkg.executables[0] ? `${pkg.executables[0]} --help` : ""}`.trim() },
    { label: "Slurm", content: `# Pyxis pulls from Harbor and starts the image unprivileged\n${c.srunRegistry}` },
    { label: "Slurm (staged)", content: `# Same image, from the pre-staged squashfs on CephFS (fastest start)\n${c.srunSqsh}` },
    { label: "Image", content: `${c.pull}\n# digest ${info.digest}` },
  ];
  panel.hint = target === "generic"
    ? "The module picks the right image for the node it runs on."
    : `This is the ${TARGET_LABEL[target]} image. The module picks the right image for the node it runs on.`;
  return panel;
}

function pipeline(info) {
  const failedAt = info.status === "failed" ? STAGE_OF[info.stage] : null;
  const reached = info.status === "published" ? STAGES.length
    : failedAt ? STAGES.indexOf(failedAt) : info.status === "running" ? 2 : 0;
  return h("ol", { class: "stages", "aria-label": "Pipeline stages" },
    STAGES.map((s, i) => h("li", {
      class: "stage",
      "data-state": failedAt === s ? "failed" : i < reached ? "done" : info.status === "running" && i === reached ? "active" : "todo",
    }, h("span", { class: "stage-dot", "aria-hidden": "true" }), h("span", { text: s }))));
}

function targetCard(pkg, version, target, info, select, selected) {
  const published = info.status === "published";
  const body = [
    h("div", { class: "tc-head" },
      h("span", { class: "tc-name", text: TARGET_LABEL[target] }),
      h("span", { class: "status", "data-status": info.status, text: STATUS_LABEL[info.status] || info.status })),
    h("p", { class: "tc-hw", text: TARGET_HW[target] }),
    pipeline(info),
  ];
  if (published) {
    body.push(h("dl", { class: "tc-facts" },
      h("dt", { text: "Tag" }), h("dd", { class: "mono", text: `${pkg.name}:${version}-${target}` }),
      h("dt", { text: "Digest" }), h("dd", { class: "mono", title: info.digest, text: shortDigest(info.digest) }),
      info.test && [h("dt", { text: "Smoke test" }), h("dd", { text: info.test })],
      info.built && [h("dt", { text: "Built" }), h("dd", { text: info.built })]));
  } else if (info.status === "failed") {
    body.push(h("p", { class: "tc-note", text: `Failed at the ${info.stage} stage. The public failure record arrives with the community layer.` }));
  }
  const attrs = { class: "tc", "data-status": info.status };
  if (!published) return h("div", attrs, body);
  return h("button", {
    ...attrs, type: "button", "aria-pressed": selected ? "true" : "false",
    "aria-label": `Show commands for ${TARGET_LABEL[target]}`, on: { click: select },
  }, body);
}

export function renderPackage({ name, setTitle }) {
  const view = h("article", { class: "pkgpage", "aria-busy": "true" },
    h("a", { class: "back", href: "#/" }, icon("right", "icon back-icon"), "Catalog"),
    h("div", { class: "pkgpage-skel", "aria-hidden": "true" }));
  setTitle(name);

  getPackage(name).then((pkg) => {
    setTitle(pkg.title);
    view.removeAttribute("aria-busy");
    let sel = null; // {version, target}
    for (const v of pkg.versions) {
      for (const [t, info] of Object.entries(v.targets)) {
        if (!sel && info.status === "published") sel = { version: v.version, target: t };
      }
    }
    const versionsEl = h("div", { class: "versions" });
    const cmdEl = h("div", { class: "cmds" });

    function paint() {
      versionsEl.replaceChildren(...pkg.versions.map((v) => h("section", { class: "version" },
        h("h3", { class: "version-h" }, h("span", { class: "mono", text: v.version }),
          v === pkg.versions[0] && h("span", { class: "chip chip-accent", text: "latest" })),
        h("div", { class: "tcs" }, ["vr", "rtx", "generic"].filter((t) => v.targets[t]).map((t) =>
          targetCard(pkg, v.version, t, v.targets[t],
            () => { sel = { version: v.version, target: t }; paint(); },
            sel && sel.version === v.version && sel.target === t))))));
      if (sel) {
        const info = pkg.versions.find((v) => v.version === sel.version).targets[sel.target];
        cmdEl.replaceChildren(
          h("h2", { class: "section-h" }, "Run ", h("span", { class: "mono", text: `${pkg.name} ${sel.version}` }),
            h("span", { class: "muted", text: ` · ${TARGET_LABEL[sel.target]}` })),
          commandPanel(pkg, sel.version, sel.target, info));
      } else {
        cmdEl.replaceChildren(h("div", { class: "notice" }, icon("bell"),
          h("p", { text: pkg.versions.length
            ? "No build of this package is available yet. Builds appear here as soon as they pass their smoke test on real GPUs."
            : "This package is planned. Its version is chosen when it is built." })));
      }
    }
    paint();

    const upstream = Object.entries(pkg.aliases);
    view.replaceChildren(
      h("a", { class: "back", href: "#/" }, icon("right", "icon back-icon"), "Catalog"),
      h("header", { class: "pkgpage-head" },
        h("div", { class: "pkgpage-title" },
          h("h1", { text: pkg.title }),
          pkg.visibility === "restricted" && h("span", { class: "chip chip-warn" }, icon("lock"), "Restricted · sysadmins only")),
        h("p", { class: "lede", text: pkg.description }),
        h("div", { class: "pkg-meta" },
          pkg.source && h("span", { class: "chip", "data-source": pkg.source }, SOURCE_LABEL[pkg.source]),
          pkg.tags.map((t) => h("a", { class: "chip chip-tag", href: `#/?q=${encodeURIComponent(t)}`, text: t })),
          targetDots(pkg.targets)),
        h("dl", { class: "facts" },
          pkg.homepage && [h("dt", { text: "Homepage" }), h("dd", {}, h("a", { href: pkg.homepage, rel: "noopener noreferrer", target: "_blank", text: pkg.homepage.replace(/^https:\/\//, "") }))],
          pkg.executables.length > 0 && [h("dt", { text: "Commands" }), h("dd", {}, pkg.executables.map((e) => h("code", { class: "exe", text: e })))],
          upstream.length > 0 && [h("dt", { text: "Upstream names" }), h("dd", {}, upstream.map(([s, n]) => h("span", { class: "alias" }, h("span", { class: "muted", text: `${s} ` }), h("code", { text: n }))))])),
      cmdEl,
      pkg.versions.length > 0 && h("h2", { class: "section-h", text: "Versions and targets" }),
      versionsEl);
  }).catch((err) => {
    view.removeAttribute("aria-busy");
    view.replaceChildren(
      h("a", { class: "back", href: "#/" }, icon("right", "icon back-icon"), "Catalog"),
      h("div", { class: "empty" },
        h("p", { class: "empty-title", text: err.status === 404 ? "No such package" : "Could not load this package" }),
        h("p", { text: err.status === 404 ? `There is no package called “${name}” in the catalog.` : err.message })));
  });
  return view;
}
