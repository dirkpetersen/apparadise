import { getDemo, getMe, logout } from "./api.js";
import { renderLogin } from "./views/login.js";
import { renderShell } from "./views/shell.js";
import { renderCatalog } from "./views/catalog.js";
import { renderPackage } from "./views/package.js";
import { renderAdmin } from "./views/admin.js";
import "./components/confirm-dialog.js";
import { h } from "./dom.js";
import { toast } from "./components/toast.js";
import "./components/code-panel.js";

// Hash routes: "#/" catalog, "#/p/<name>" package page, "#/admin" build console
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
  const page = hash === "#/admin"
    ? renderAdmin({ me, setTitle })
    : m
      ? renderPackage({ name: decodeURIComponent(m[1]), setTitle })
      : renderCatalog({ query: new URLSearchParams(hash.split("?")[1] || "").get("q") || "", setTitle });
  swap(renderShell({ appName, me, onLogout: doLogout, onSignIn, content: page }));
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
