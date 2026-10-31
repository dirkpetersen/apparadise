import { h } from "./dom.js";
import { icon } from "./icons.js";
import { buildEvents } from "./api.js";

const MAX_CHARS = 512 * 1024; // keep the page light; the head of a huge log is dropped

// A live build log: follows the tail while the build runs, stops at "end".
// Scrolling up pauses auto-follow; scrolling back to the bottom resumes it.
// The EventSource closes when the element leaves the page.
export function liveLog(id, { title = `Build ${id}`, onClose } = {}) {
  const pre = h("pre", { class: "log live-log", tabindex: "0", "aria-label": `Log of build ${id}` });
  const state = h("span", { class: "live-state", "data-state": "connecting", text: "Connecting…" });
  const box = h("div", { class: "livebox" },
    h("div", { class: "log-head" },
      h("strong", { text: title }), state,
      onClose && h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close log",
        on: { click: () => { es.close(); onClose(); } } }, icon("x"))),
    pre);
  let follow = true;
  let size = 0;
  pre.addEventListener("scroll", () => { follow = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8; });
  const es = buildEvents(id);
  const set = (s, text) => { state.dataset.state = s; state.textContent = text; };
  es.addEventListener("status", (e) => {
    const { status, stage } = JSON.parse(e.data);
    const live = status === "running" || status === "queued";
    set(live ? "live" : "done", live ? `${status}${stage ? ` · ${stage}` : ""} · live` : `${status}${stage ? ` at ${stage}` : ""}`);
  });
  es.addEventListener("log", (e) => {
    pre.append(document.createTextNode(`${e.data}\n`));
    size += e.data.length + 1;
    if (size > MAX_CHARS) { pre.textContent = pre.textContent.slice(-MAX_CHARS / 2); size = pre.textContent.length; }
    if (follow) pre.scrollTop = pre.scrollHeight;
  });
  es.addEventListener("end", (e) => { es.close(); set("done", `${e.data} · log complete`); });
  es.addEventListener("error", () => {
    if (es.readyState === EventSource.CLOSED) set("error", "Not available");
    else set("connecting", "Reconnecting…");
  });
  // Close the stream once the box is gone (route change, re-render).
  const watch = setInterval(() => { if (!box.isConnected) { es.close(); clearInterval(watch); } }, 2000);
  return box;
}
