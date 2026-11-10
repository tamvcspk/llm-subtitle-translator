// Console entry point: hash routing, the shared poll, and chrome that every
// view shares (sidebar badges and health, quota banner, theme).
import { formatDateTime, nowSeconds } from "./format.js";
import { refreshQueue, refreshStatus, store, subscribe } from "./store.js";
import { hydrateIcons, icon, toast, updateClocks } from "./ui.js";
import dashboard from "./views/dashboard.js";
import queue from "./views/queue.js";
import { scanView, wantedView } from "./views/candidates.js";
import settings from "./views/settings.js";
import system from "./views/system.js";
import logs from "./views/logs.js";

const VIEWS = { dashboard, queue, wanted: wantedView, scan: scanView, settings, system, logs };
const POLL_MS = 5000;

const initialized = new Set();
let currentView = null;

/** "#queue/failed" → { view: "queue", param: "failed" } */
function viewFromHash() {
  const [view, param] = window.location.hash.slice(1).split("/");
  return Object.hasOwn(VIEWS, view) ? { view, param } : { view: "dashboard" };
}

async function switchView({ view, param }) {
  const def = VIEWS[view];
  if (currentView && currentView !== view) VIEWS[currentView].leave?.();
  currentView = view;

  document.querySelectorAll(".view").forEach((el) => { el.hidden = el.dataset.view !== view; });
  document.querySelectorAll(".nav-item").forEach((el) => {
    if (el.dataset.view === view) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  document.getElementById("view-title").textContent = def.title;
  document.getElementById("view-subtitle").textContent = def.subtitle;
  document.title = `${def.title} · GST Bazarr`;

  try {
    if (!initialized.has(view)) {
      initialized.add(view);
      def.init?.();
    }
    await def.enter?.(param);
  } catch (error) {
    toast(error.message, "error");
  }
}

// ---- Sidebar and banner -------------------------------------------------

function setHealth(id, tone, value, title = "") {
  const el = document.getElementById(id);
  el.dataset.tone = tone;
  el.title = title;
  el.querySelector(".health-value").textContent = value;
}

function setBadge(id, count) {
  const el = document.getElementById(id);
  el.hidden = !count;
  el.textContent = count;
}

function activePause() {
  const pause = store.queue?.daily_quota_pause ?? store.status?.daily_quota_pause;
  const until = Number(pause?.retry_at);
  return Number.isFinite(until) && until > nowSeconds() ? until : null;
}

function renderQuotaBanner() {
  const until = activePause();
  const banner = document.getElementById("quota-banner");
  banner.hidden = !until;
  if (!until) return;
  document.getElementById("quota-until").textContent = formatDateTime(until);
  document.getElementById("quota-countdown").dataset.until = until;
  updateClocks(banner);
}

function renderWorkerHealth() {
  if (store.queueError) {
    setHealth("health-worker", "danger", "Unreachable", store.queueError.message);
    return;
  }
  const counts = store.queue?.counts;
  if (!counts) return;
  setBadge("nav-active-count", (counts.pending || 0) + (counts.processing || 0) + (counts.deferred || 0));
  setBadge("nav-failed-count", counts.failed || 0);
  if (activePause()) setHealth("health-worker", "warn", "Quota paused");
  else if (counts.processing) setHealth("health-worker", "busy", "Translating");
  else if (counts.pending) setHealth("health-worker", "ok", "Starting soon");
  else setHealth("health-worker", "ok", "Idle");
}

function renderBazarrHealth(status) {
  if (!status) return;
  const error = status.bazarr?.error;
  setHealth("health-bazarr", error ? "danger" : "ok", error ? "Unreachable" : "Connected", error || status.bazarr_url);
}

subscribe("queue", () => { renderWorkerHealth(); renderQuotaBanner(); });
subscribe("status", (status) => { renderBazarrHealth(status); renderQuotaBanner(); });

// ---- Theme ----------------------------------------------------------------

const THEMES = [
  ["system", "monitor", "System theme"],
  ["light", "sun", "Light theme"],
  ["dark", "moon", "Dark theme"],
];

function readTheme() {
  try {
    return localStorage.getItem("gst-theme") || "system";
  } catch {
    return "system";
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
  const [, iconName, label] = THEMES.find(([name]) => name === theme) || THEMES[0];
  document.getElementById("theme-toggle").replaceChildren(icon(iconName), label);
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  const index = THEMES.findIndex(([name]) => name === readTheme());
  const next = THEMES[(index + 1) % THEMES.length][0];
  try {
    localStorage.setItem("gst-theme", next);
  } catch {
    // Theme still applies for this page load.
  }
  applyTheme(next);
});

// ---- Polling ------------------------------------------------------------

let ticks = 0;

function poll() {
  if (document.hidden) return;
  ticks += 1;
  // Queue-focused views refresh every tick; elsewhere the sidebar badges
  // only need an occasional update.
  if (currentView === "dashboard" || currentView === "queue" || ticks % 3 === 0) {
    refreshQueue().catch(() => {});
  }
  VIEWS[currentView]?.tick?.();
}

setInterval(poll, POLL_MS);
setInterval(() => updateClocks(), 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poll();
});

// ---- Start ----------------------------------------------------------------

hydrateIcons();
applyTheme(readTheme());

window.addEventListener("hashchange", () => switchView(viewFromHash()));
switchView(viewFromHash());
refreshQueue().catch(() => {});
refreshStatus().catch((error) => setHealth("health-bazarr", "danger", "Unknown", error.message));
