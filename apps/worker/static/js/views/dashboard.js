import { jobCard, QUEUE_STATES } from "../jobs.js";
import { formatDateTime, nowSeconds } from "../format.js";
import { enabledLanguages, languageLabel, loadLanguages, refreshQueue, refreshStatus, store, subscribe } from "../store.js";
import { emptyState, h, icon, replaceKeepingOpen } from "../ui.js";

let active = false;

function renderQueue(snapshot) {
  if (!active || !snapshot) return;
  const counts = snapshot.counts || {};
  QUEUE_STATES.forEach((state) => {
    document.getElementById(`stat-${state}`).textContent = counts[state] || 0;
  });
  document.querySelector('.stat[data-state="failed"]').classList.toggle("stat-danger", Boolean(counts.failed));

  const processing = snapshot.processing || [];
  replaceKeepingOpen(document.getElementById("dash-active"), processing.length
    ? processing.map((job) => jobCard(job, "processing"))
    : [emptyState("Idle", counts.pending ? "Jobs are waiting to start." : "No translation is running.", "clock")]);

  const failed = (snapshot.failed || []).slice(0, 5);
  replaceKeepingOpen(document.getElementById("dash-failed"), failed.length
    ? failed.map((job) => jobCard(job, "failed", { compact: true }))
    : [emptyState("All clear", "No failed jobs.", "check")]);
}

function serviceRow(label, tone, value, detail) {
  return [
    h("dt", {}, label),
    h("dd", {},
      h("span", { class: `status status-${tone}` }, h("span", { class: "dot" }), value),
      detail && h("span", { class: "service-detail", title: detail, text: detail })),
  ];
}

function renderServices() {
  if (!active) return;
  const status = store.status;
  const list = document.getElementById("dash-services");
  if (!status) {
    list.replaceChildren(h("div", { class: "muted pad", text: "Checking services…" }));
    return;
  }
  const settings = status.settings || {};
  const bazarrError = status.bazarr?.error;
  const geminiKeys = [settings.gemini_api_key_configured, settings.gemini_api_key2_configured].filter(Boolean).length;
  const pause = store.queue?.daily_quota_pause ?? status.daily_quota_pause;
  const paused = pause?.retry_at && pause.retry_at > nowSeconds();
  list.replaceChildren(
    ...serviceRow("Bazarr", bazarrError ? "danger" : "ok", bazarrError ? "Unreachable" : "Connected", bazarrError || status.bazarr_url),
    ...serviceRow("Gemini", geminiKeys ? "ok" : "danger",
      geminiKeys ? `${geminiKeys} API key${geminiKeys > 1 ? "s" : ""}` : "No API key", settings.gst_model),
    ...serviceRow("Daily quota", paused ? "warn" : "ok", paused ? "Paused" : "Available",
      paused ? `Until ${formatDateTime(pause.retry_at)}` : ""),
    ...serviceRow("TMDB", settings.tmdb_api_key_configured ? "ok" : "muted",
      settings.tmdb_api_key_configured ? "Configured" : "Not configured", ""),
  );
}

function languageChips(items) {
  const enabled = enabledLanguages(items);
  if (!enabled.length) return h("span", { class: "muted", text: "None" });
  return h("div", { class: "chip-list" }, enabled.map((item) => h("span", { class: "chip", text: languageLabel(item) })));
}

function renderLanguages() {
  if (!active) return;
  const settings = store.status?.settings;
  const container = document.getElementById("dash-languages");
  if (!settings) {
    container.replaceChildren();
    return;
  }
  container.replaceChildren(
    h("div", {}, h("div", { class: "eyebrow", text: "From" }), languageChips(settings.source_languages)),
    h("div", { class: "lang-arrow" }, icon("arrow")),
    h("div", {}, h("div", { class: "eyebrow", text: "Into" }), languageChips(settings.target_languages)),
  );
}

export default {
  title: "Dashboard",
  subtitle: "Translation activity and service health at a glance.",

  init() {
    subscribe("queue", (snapshot) => { renderQueue(snapshot); renderServices(); });
    subscribe("status", () => { renderServices(); renderLanguages(); });
    subscribe("languages", renderLanguages);
  },

  async enter() {
    active = true;
    renderQueue(store.queue);
    renderServices();
    renderLanguages();
    loadLanguages().catch(() => {});
    await Promise.all([refreshQueue(), refreshStatus()]);
  },

  leave() {
    active = false;
  },
};
