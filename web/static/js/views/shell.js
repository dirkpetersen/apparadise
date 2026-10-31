import { h } from "../dom.js";
import { icon } from "../icons.js";
import "../components/menu-button.js";

// Page frame: header (brand, theme, sign-in or user menu), main content, footer.
const THEMES = ["system", "light", "dark"];
const THEME_ICON = { system: "monitor", light: "sun", dark: "moon" };

function currentTheme() {
  const t = document.documentElement.dataset.theme;
  return t === "light" || t === "dark" ? t : "system";
}

function applyTheme(t) {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  try {
    if (t === "system") localStorage.removeItem("apparadise.theme");
    else localStorage.setItem("apparadise.theme", t);
  } catch { /* storage blocked: the choice lasts for this page only */ }
}

function themeButton() {
  const btn = h("button", { class: "btn btn-icon btn-ghost", type: "button" });
  const paint = () => {
    const t = currentTheme();
    btn.replaceChildren(icon(THEME_ICON[t]));
    btn.setAttribute("aria-label", `Theme: ${t}. Switch theme`);
    btn.title = `Theme: ${t}`;
  };
  btn.addEventListener("click", () => {
    applyTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]);
    paint();
  });
  paint();
  return btn;
}

function initials(name) {
  const parts = (name || "?").trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts.length > 1 ? parts.at(-1)[0] : "")).toUpperCase() || "?";
}

function userMenu(me, onLogout) {
  const mb = h("menu-button", {
    class: "user",
    label: me.displayName || me.username,
    ariaLabel: `Account menu for ${me.displayName || me.username}`,
  });
  mb.items = [
    { label: me.isSysadmin ? `${me.username} · sysadmin` : me.username, icon: "user", disabled: true, reason: me.email || "" },
    { label: "Sign out", icon: "logout", separatorBefore: true, onSelect: onLogout },
  ];
  return h("div", { class: "user-wrap" },
    h("span", { class: "avatar", "aria-hidden": "true", text: initials(me.displayName || me.username) }), mb);
}

// Which nav item is current. A package page belongs to no section (it is reached from both).
function section() {
  const h = location.hash || "#/";
  if (h === "#/admin") return "admin";
  if (h === "#/registry" || h.startsWith("#/registry/")) return "registry";
  if (h.startsWith("#/failures") || h.startsWith("#/f/")) return "failures";
  return h.startsWith("#/p/") ? "" : "catalog";
}

export function renderShell({ appName, me, onLogout, onSignIn, content }) {
  const right = me
    ? userMenu(me, onLogout)
    : h("button", { class: "btn", type: "button", on: { click: onSignIn } }, icon("user"), h("span", { text: "Sign in" }));
  return h("div", { class: "shell" },
    h("a", { class: "skip-link", href: "#main", text: "Skip to content", on: { click: (e) => {
      e.preventDefault(); document.getElementById("main")?.focus();
    } } }),
    h("header", { class: "hdr" },
      h("div", { class: "hdr-in" },
        h("a", { class: "brand", href: "#/" },
          h("img", { src: "img/mark.svg", alt: "", width: 30, height: 30 }),
          h("span", { class: "brand-name", text: appName })),
        h("nav", { class: "hdr-nav", "aria-label": "Main" },
          h("a", { href: "#/", text: "Catalog", "aria-current": section() === "catalog" ? "page" : null }),
          h("a", { href: "#/registry", text: "Registry", "aria-current": section() === "registry" ? "page" : null }),
          h("a", { href: "#/failures", text: "Failures", "aria-current": section() === "failures" ? "page" : null }),
          me?.isSysadmin && h("a", { href: "#/admin", text: "Admin", "aria-current": section() === "admin" ? "page" : null })),
        themeButton(),
        right)),
    h("main", { class: "main", id: "main", tabindex: "-1" }, content),
    h("footer", { class: "ftr" },
      h("div", { class: "ftr-in" },
        h("span", { text: "One image, every front door: Slurm, Lmod and Run:ai." }),
        h("span", { class: "ftr-targets", text: "vr · Vera Rubin NVL72   rtx · RTX PRO 6000 farm   generic · portable" }))));
}
