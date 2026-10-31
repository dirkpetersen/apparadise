import { h, replace } from "../dom.js";
import { icon } from "../icons.js";
import { createRequest, getChatStatus, queueBuild, sendChat } from "../api.js";
import { SOURCE_LABEL, TARGET_LABEL } from "./catalog.js";

// "#/ask": the request chatbot. Claude runs on the server with five tools; anything that
// would change state comes back as a proposal, which only the user's click carries out.
const SUGGESTIONS = [
  "Is GROMACS available on the Vera Rubin nodes?",
  "I need LAMMPS with KOKKOS for the RTX farm.",
  "Why did the vLLM build fail on vr?",
];

// Assistant text: paragraphs, lists, ``` fenced blocks, `inline code` and **bold**. Built
// with h(), so nothing the model writes is ever parsed as HTML.
function inline(text) {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/).map((part) => {
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return h("code", { text: part.slice(1, -1) });
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) return h("strong", { text: part.slice(2, -2) });
    return part;
  });
}
function render(text) {
  const out = [];
  const parts = text.split(/```[a-z]*\n?/);
  parts.forEach((part, i) => {
    if (i % 2 === 1) { out.push(h("pre", { class: "chat-code" }, part.replace(/\n$/, ""))); return; }
    for (const para of part.split(/\n{2,}/)) {
      if (!para.trim()) continue;
      const lines = para.split("\n");
      if (lines.every((l) => /^\s*[-*] /.test(l))) {
        out.push(h("ul", {}, lines.map((l) => h("li", {}, inline(l.replace(/^\s*[-*] /, ""))))));
      } else {
        out.push(h("p", {}, lines.flatMap((l, j) => (j ? [h("br"), ...inline(l)] : inline(l)))));
      }
    }
  });
  return out;
}

function proposalCard(p, me) {
  const result = h("div", { class: "chat-prop-result", "aria-live": "polite" });
  const isBuild = p.kind === "build";
  const btn = h("button", { class: "btn btn-primary btn-sm", type: "button" }, icon("play"),
    h("span", { text: isBuild ? "Queue build" : "Request build" }));
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const r = isBuild
        ? await queueBuild({ package: p.name, version: p.version, targets: p.targets, ...(p.source ? { source: p.source } : {}) })
        : await createRequest({ name: p.name, targets: p.targets, ...(p.version ? { version: p.version } : {}), ...(p.source ? { source: p.source } : {}) });
      replace(result, [h("p", {}, icon(r.queued.length ? "check" : "bell"),
        r.queued.length ? `Queued ${r.queued.length} build job${r.queued.length === 1 ? "" : "s"}${r.version ? ` of ${r.version}` : ""}. ` : "Nothing new to build. ",
        h("a", { href: "#/requests", text: "See requests" })),
      r.skipped.map((s) => h("p", { class: "muted", text: `${TARGET_LABEL[s.target]}: ${s.reason}` }))]);
      btn.replaceChildren(icon("check"), h("span", { text: "Done" }));
    } catch (err) {
      result.replaceChildren(h("p", { class: "form-error", role: "alert" }, icon("alert"), h("span", { text: err.message })));
      btn.disabled = false;
    }
  });
  const src = p.source || p.resolution?.source;
  return h("div", { class: "chat-prop" },
    h("div", { class: "chat-prop-main" },
      h("p", { class: "chat-prop-title" }, h("span", { class: "mono", text: p.name }),
        h("span", { class: "muted", text: p.version ? ` ${p.version}` : " newest" })),
      h("p", { class: "chat-prop-meta", text: [p.targets.map((t) => TARGET_LABEL[t] || t).join(" · "), src && SOURCE_LABEL[src]].filter(Boolean).join("  ·  ") }),
      !isBuild && !me?.isSysadmin && h("p", { class: "chat-prop-note", text: "Counts against your weekly quota." })),
    btn, result);
}

export function renderChat({ me, setTitle }) {
  setTitle("Ask");
  let conversation = null;
  let busy = false;
  const thread = h("ol", { class: "chat-thread", "aria-live": "polite", "aria-label": "Conversation" });
  const input = h("textarea", { class: "input chat-input", id: "chat-in", rows: "2", maxlength: "4000", placeholder: "Ask about a package, a version, a failed build…", "aria-label": "Your message" });
  const send = h("button", { class: "btn btn-primary", type: "submit", "aria-label": "Send" }, icon("up"), h("span", { text: "Send" }));
  const reset = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, icon("refresh"), h("span", { text: "New conversation" }));
  const body = h("div", { class: "chat" });

  const scroll = () => thread.lastElementChild?.scrollIntoView({ block: "end", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  function bubble(role, children) {
    const li = h("li", { class: "chat-msg", "data-role": role }, h("div", { class: "chat-bubble" }, children));
    thread.append(li);
    scroll();
    return li;
  }
  function empty() {
    thread.replaceChildren(h("li", { class: "chat-empty" },
      h("p", { class: "muted", text: "Try one of these:" }),
      h("div", { class: "chat-sugg" }, SUGGESTIONS.map((s) => h("button", { class: "filter", type: "button", text: s, on: { click: () => { input.value = s; ask(); } } })))));
  }
  async function ask() {
    const text = input.value.trim();
    if (!text || busy) return;
    if (thread.querySelector(".chat-empty")) thread.replaceChildren();
    busy = true;
    send.disabled = true;
    input.value = "";
    bubble("user", h("p", { text }));
    const pending = bubble("assistant", h("p", { class: "chat-typing", "aria-label": "Thinking" }, h("span"), h("span"), h("span")));
    try {
      const r = await sendChat(text, conversation);
      conversation = r.conversation;
      pending.firstChild.replaceChildren(...render(r.reply),
        ...(r.proposals.length ? [h("div", { class: "chat-props" }, r.proposals.map((p) => proposalCard(p, me)))] : []));
    } catch (err) {
      pending.dataset.role = "error";
      pending.firstChild.replaceChildren(h("p", {}, icon("alert"), h("span", { text: ` ${err.message}` })));
    } finally {
      busy = false;
      send.disabled = false;
      input.focus();
      scroll();
    }
  }
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(); } });
  reset.addEventListener("click", () => { conversation = null; empty(); input.focus(); });

  const hero = h("section", { class: "hero hero-compact" },
    h("p", { class: "eyebrow", text: "Assistant" }),
    h("h1", {}, "Ask about CUDA software. ", h("em", { text: "Get the exact command." })),
    h("p", { text: "The assistant searches the catalog and the Spack, EasyBuild and conda-forge indexes, explains failed builds, and drafts build requests that you confirm with one click." }));

  if (!me) {
    body.append(h("div", { class: "notice" }, icon("user"), h("div", {},
      h("p", { text: "Sign in with your AD account to use the assistant." }),
      h("p", {}, h("button", { class: "btn btn-sm", type: "button", on: { click: () => window.dispatchEvent(new CustomEvent("apparadise:signin")) } }, "Sign in")))));
    return h("div", { class: "catalog chatpage" }, hero, body);
  }
  getChatStatus().then((s) => {
    if (!s.enabled) {
      body.replaceChildren(h("div", { class: "notice" }, icon("bell"), h("p", { text: "The assistant is switched off on this server (no ANTHROPIC_API_KEY). The catalog, requests and failure pages work without it." })));
      return;
    }
    empty();
    body.replaceChildren(
      h("div", { class: "chat-head" }, h("span", { class: "muted chat-model", text: `Claude · ${s.model}` }), reset),
      thread,
      h("form", { class: "chat-form", on: { submit: (e) => { e.preventDefault(); ask(); } } }, input, send),
      h("p", { class: "chat-fine", text: "Answers can be wrong: check the command before you run a long job. Your messages are kept for two hours." }));
    input.focus();
  }).catch((err) => body.replaceChildren(h("div", { class: "form-error", role: "alert" }, icon("alert"), h("span", { text: err.message }))));
  return h("div", { class: "catalog chatpage" }, hero, body);
}
