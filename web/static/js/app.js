import { getDemo, getMe, logout } from "./api.js";
import { renderLogin } from "./views/login.js";
import { renderShell } from "./views/shell.js";
import { renderCatalog } from "./views/catalog.js";
import { renderPackage } from "./views/package.js";
import { renderAdmin } from "./views/admin.js";
import { renderRegistry, renderRepository } from "./views/registry.js";
import { renderFailure, renderFailures } from "./views/failures.js";
import { renderRequests } from "./views/requests.js";
import { renderChat } from "./views/chat.js";
import { liveLog } from "./livelog.js";
import "./components/confirm-dialog.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./components/toast.js";
import "./components/code-panel.js";

// Hash routes: "#/" catalog, "#/p/<name>" package page, "#/registry" (and
// "#/registry/<project>/<repo>") what the registry holds, "#/failures" (and "#/f/<id>") the
// public failure database, "#/ask" the request chatbot, "#/requests" software requests, "#/b/<id>" a live build log,
// "#/admin" build console
// (sysadmins; the server enforces the role), "#/login" sign-in.
// The catalog is public; signing in only matters for sysadmins (restricted
// packages) and, in later phases, for requests and votes.
const appName = document.title || "apparadise";
const root = document.getElementById("app");
let me = null;
let current = "";

function swap(node) {
  const apply = () => root.replaceChildren(node);
  if (document.startViewTransition && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.startViewTransition(apply);
  } else {
    apply();
  }
}

function route() {
  const hash = location.hash || "#/";
  if (hash === current) return;
  current = hash;
  if (hash === "#/login") {
    if (me) return go("#/");
    document.title = `Sign in · ${appName}`;
    swap(renderLogin({ appName, onSuccess: async (user) => { me = user; go(sessionStorageGet() || "#/"); } }));
    return;
  }
  const m = hash.match(/^#\/p\/([^/?#]+)$/);
  const r = hash.match(/^#\/registry\/([^/?#]+)\/([^?#]+)$/);
  const f = hash.match(/^#\/f\/(\d+)$/);
  const b = hash.match(/^#\/b\/(\d+)$/);
  const query = new URLSearchParams(hash.split("?")[1] || "").get("q") || "";
  const page = hash === "#/admin"
    ? renderAdmin({ me, setTitle })
    : b
      ? buildPage(b[1])
    : hash === "#/ask"
      ? renderChat({ me, setTitle })
    : hash === "#/requests"
      ? renderRequests({ me, setTitle })
    : hash.split("?")[0] === "#/failures"
      ? renderFailures({ me, query, setTitle })
    : f
      ? renderFailure({ id: f[1], me, setTitle })
    : hash === "#/registry"
      ? renderRegistry({ setTitle })
      : r
        ? renderRepository({ project: decodeURIComponent(r[1]), name: r[2].split("/").map(decodeURIComponent).join("/"), setTitle })
        : m
          ? renderPackage({ name: decodeURIComponent(m[1]), me, setTitle })
          : renderCatalog({ query, setTitle });
  swap(renderShell({ appName, me, onLogout: doLogout, onSignIn, content: page }));
}

// "#/b/<id>": one build's live log (its requester sees it scrubbed, sysadmins raw).
function buildPage(id) {
  setTitle(`Build ${id}`);
  return h("article", { class: "pkgpage" },
    h("a", { class: "back", href: "#/requests" }, icon("right", "icon back-icon"), "Requests"),
    liveLog(id));
}

function setTitle(t) { document.title = t ? `${t} · ${appName}` : appName; }

function go(hash) {
  if (location.hash === hash) { current = ""; route(); } else location.hash = hash;
}

// Remember where to return to after signing in (per tab, no secrets).
function sessionStorageGet() {
  try { const v = sessionStorage.getItem("apparadise.return"); sessionStorage.removeItem("apparadise.return"); return v; } catch { return null; }
}
function onSignIn() {
  try { sessionStorage.setItem("apparadise.return", location.hash || "#/"); } catch { /* storage blocked */ }
  go("#/login");
}

async function doLogout() {
  me = null;
  try { await logout(); } catch { /* the session is gone either way */ }
  current = "";
  route();
  toast.show("Signed out");
}

window.addEventListener("hashchange", route);
// Views that need a signed-in user (e.g. the upvote button) ask for sign-in this way.
window.addEventListener("apparadise:signin", onSignIn);
window.addEventListener("apparadise:unauthorized", () => {
  if (me) { me = null; current = ""; route(); toast.show("Your session has ended."); }
});

getDemo()
  .then(({ demo }) => {
    if (!demo) return;
    document.body.classList.add("has-demo");
    document.body.append(h("div", { class: "demo-banner", role: "note" },
      h("strong", {}, "Demo mode"), " fake directory, sample catalog, temporary data"));
  })
  .catch(() => {});

(async function start() {
  try { me = await getMe(); } catch (err) { if (err.status !== 401) toast.error(err.message); }
  route();
})();
