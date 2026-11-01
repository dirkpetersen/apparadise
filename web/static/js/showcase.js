import { h } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./components/toast.js";
import { targetDots, SOURCE_LABEL } from "./views/catalog.js";
import "./components/code-panel.js";

const $ = (id) => document.getElementById(id);
const TOKENS = ["--bg", "--surface", "--surface-2", "--text", "--text-muted", "--accent", "--accent-soft",
  "--success", "--info", "--warn", "--danger", "--pending", "--code-bg"];

$("swatches").append(...TOKENS.map((t) => {
  const chip = h("div", { class: "swatch-chip" });
  chip.style.background = `var(${t})`; // CSSOM, allowed by the CSP
  return h("div", { class: "swatch" }, chip, h("code", { text: t }));
}));

for (const t of ["system", "light", "dark"]) {
  $("theme-row").append(h("button", { class: "btn", type: "button", on: { click: () => {
    if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  } } }, icon({ system: "monitor", light: "sun", dark: "moon" }[t]), t));
}

$("chips").append(
  ...Object.entries(SOURCE_LABEL).map(([k, v]) => h("span", { class: "chip", "data-source": k, text: v })),
  h("span", { class: "chip chip-accent", text: "latest" }),
  h("span", { class: "chip chip-warn" }, icon("lock"), "Restricted"),
  h("a", { class: "chip chip-tag", href: "#", text: "cryo-em" }));
$("statuses").append(...["published", "running", "queued", "failed", "planned"].map((s) =>
  h("span", { class: "status", "data-status": s, text: s })),
  targetDots({ vr: "running", rtx: "published", generic: "failed" }));

const sample = [
  ["GROMACS", "Molecular dynamics for biomolecular systems.", "spack", "2026.3", { vr: "running", rtx: "published" }],
  ["AlphaFold 3", "Protein structure prediction. Restricted weights.", "easybuild", "3.0.1", { vr: "planned", rtx: "published" }, true],
  ["vLLM", "High-throughput LLM inference.", "pixi", null, { vr: "planned", rtx: "queued" }],
];
$("cards").append(...sample.map(([title, desc, src, ver, targets, restricted]) => h("li", { class: "pkg" },
  h("a", { class: "pkg-link", href: "#" },
    h("div", { class: "pkg-top" }, h("h3", { class: "pkg-title", text: title }),
      restricted && h("span", { class: "chip chip-warn" }, icon("lock"), "Restricted")),
    h("p", { class: "pkg-desc", text: desc }),
    h("div", { class: "pkg-meta" }, h("span", { class: "chip", "data-source": src, text: SOURCE_LABEL[src] }),
      ver ? h("span", { class: "pkg-ver", text: ver }) : h("span", { class: "pkg-ver pkg-ver-none", text: "version at build time" }),
      targetDots(targets))))));
$("cards").append(h("li", { class: "pkg pkg-skel", "aria-hidden": "true" }));

const rail = (states) => h("ol", { class: "stages" }, ["resolve", "generate", "build", "package", "test", "publish"].map((s, i) =>
  h("li", { class: "stage", "data-state": states[i] }, h("span", { class: "stage-dot" }), h("span", { text: s }))));
$("tcs").append(
  h("button", { class: "tc", type: "button", "data-status": "published", "aria-pressed": "true" },
    h("div", { class: "tc-head" }, h("span", { class: "tc-name", text: "RTX farm" }), h("span", { class: "status", "data-status": "published", text: "Available" })),
    h("p", { class: "tc-hw", text: "x86-64 · RTX PRO 6000 Blackwell (sm_120)" }), rail(Array(6).fill("done"))),
  h("div", { class: "tc", "data-status": "running" },
    h("div", { class: "tc-head" }, h("span", { class: "tc-name", text: "Vera Rubin" }), h("span", { class: "status", "data-status": "running", text: "Building" })),
    h("p", { class: "tc-hw", text: "NVIDIA Vera (Arm) · Rubin GPU" }), rail(["done", "done", "active", "todo", "todo", "todo"])),
  h("div", { class: "tc", "data-status": "failed" },
    h("div", { class: "tc-head" }, h("span", { class: "tc-name", text: "Vera Rubin" }), h("span", { class: "status", "data-status": "failed", text: "Failed" })),
    h("p", { class: "tc-hw", text: "NVIDIA Vera (Arm) · Rubin GPU" }), rail(["done", "done", "failed", "todo", "todo", "todo"]),
    h("p", { class: "tc-note", text: "Failed at the build stage." })));

const panel = h("code-panel");
panel.tabs = [
  { label: "Module", content: "module load gromacs/2026.3\ngmx_mpi --help" },
  { label: "Slurm", content: "# Pyxis pulls from the registry\nsrun --container-image=registry.example.org#public/gromacs:2026.3-rtx gmx_mpi" },
];
panel.hint = "The module picks the right image for the node it runs on.";
$("code").append(panel);

$("misc").append(
  h("div", { class: "notice" }, icon("bell"), h("p", { text: "No build of this package is available yet." })),
  h("div", { class: "empty" }, h("p", { class: "empty-title", text: "Nothing here yet" }), h("p", { text: "Empty state." })),
  h("div", { class: "row" },
    h("button", { class: "btn", type: "button", on: { click: () => toast.show("Copied") } }, "Toast"),
    h("button", { class: "btn", type: "button", on: { click: () => toast.error("Could not load the catalog") } }, "Error toast")));
