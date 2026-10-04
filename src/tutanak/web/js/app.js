// App shell and hash router.

import { api } from "./api.js";
import { setLanguage, t } from "./i18n.js";
import { LibraryView } from "./library.js";
import { MeetingView } from "./meeting.js";
import { SettingsView } from "./settings.js";
import { clear, debounce, h, icon, toast } from "./util.js";

const root = document.getElementById("app");
const app = { settings: null, view: null, route: null, main: null, search: null, rebuild };

function parseRoute() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, queryString] = hash.split("?");
  const params = new URLSearchParams(queryString || "");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "m" && parts[1]) {
    const time = params.get("t");
    return { name: "meeting", id: parts[1], t: time === null ? null : Number(time), q: params.get("q") || "" };
  }
  if (parts[0] === "settings") return { name: "settings" };
  return { name: "library", q: params.get("q") || "" };
}

const VIEWS = { library: LibraryView, meeting: MeetingView, settings: SettingsView };

function sameView(a, b) {
  return a && b && a.name === b.name && a.id === b.id;
}

async function navigate() {
  const route = parseRoute();
  if (app.search && document.activeElement !== app.search) {
    app.search.value = route.name === "library" ? route.q : "";
  }
  if (app.view && sameView(app.route, route) && app.view.update) {
    app.route = route;
    app.view.update(route);
    return;
  }
  if (app.view && app.view.unmount) app.view.unmount();
  app.route = route;
  clear(app.main);
  window.scrollTo(0, 0);
  const view = new VIEWS[route.name](app);
  app.view = view;
  try {
    await view.mount(app.main, route);
  } catch (error) {
    if (app.view !== view) return;
    clear(app.main).append(h("div", { class: "empty" }, h("p", null, error.message), h("a", { href: "#/" }, t("back"))));
  }
}

function buildShell() {
  const search = h("input", {
    type: "search",
    class: "search-input",
    placeholder: t("search_placeholder"),
    "aria-label": t("search_placeholder"),
    autocomplete: "off",
  });
  const go = debounce(() => {
    const target = search.value.trim() ? `#/?q=${encodeURIComponent(search.value.trim())}` : "#/";
    if (location.hash === target || (target === "#/" && !location.hash)) return;
    // Typing refines the same search: do not pile up history entries.
    if (app.route && app.route.name === "library") history.replaceState(null, "", target);
    else history.pushState(null, "", target);
    navigate();
  }, 220);
  search.addEventListener("input", go);
  app.search = search;
  app.main = h("main", { class: "main" });
  clear(root).append(
    h(
      "header",
      { class: "topbar" },
      h("a", { class: "brand", href: "#/", title: t("tagline") }, h("img", { src: "/favicon.svg", alt: "", width: 26, height: 26 }), "Tutanak"),
      h("label", { class: "search" }, icon("search"), search),
      h("span", { class: "local-badge", title: t("local_hint") }, icon("lock", 14), t("local_badge")),
      h("a", { class: "icon-button", href: "#/settings", title: t("settings"), "aria-label": t("settings") }, icon("settings", 20)),
    ),
    app.main,
  );
}

function rebuild() {
  setLanguage(app.settings.ui_language);
  if (app.view && app.view.unmount) app.view.unmount();
  app.view = null;
  buildShell();
  navigate();
}

async function start() {
  try {
    app.settings = await api.get("/settings");
  } catch (error) {
    root.textContent = error.message;
    return;
  }
  window.addEventListener("hashchange", navigate);
  window.addEventListener("unhandledrejection", (event) => toast(event.reason?.message || String(event.reason), "error"));
  rebuild();
}

start();
