import { get, post } from "../api.js";
import { bindAction, confirmDialog, h, replaceKeepingOpen, toast } from "../ui.js";

const logState = { entries: [], visible: [], loading: null, snapshot: "", updated: "", truncated: false };
const $ = (id) => document.getElementById(id);

function renderLogs() {
  const level = $("log-level").value;
  const query = $("log-search").value.trim().toLowerCase();
  logState.visible = logState.entries.filter((entry) =>
    (!level || entry.level === level) &&
    `${entry.timestamp} ${entry.message} ${entry.details}`.toLowerCase().includes(query));

  const output = $("log-output");
  const previousScroll = output.scrollTop;
  const rows = logState.visible.map((entry) => h("div", { class: `log-entry log-${entry.level.toLowerCase()}` },
    h("span", { class: "log-time", text: entry.timestamp || "—" }),
    h("span", { class: "log-level", text: entry.level }),
    h("div", { class: "log-message" },
      h("div", { text: entry.message }),
      entry.details && h("details", { dataset: { key: `${entry.timestamp} ${entry.message}` } },
        h("summary", { text: "Details" }),
        h("pre", { text: entry.details })))));
  replaceKeepingOpen(output, rows.length ? rows : [h("p", {
    class: "log-empty",
    text: logState.entries.length ? "No events match your filters." : "No logs yet. Worker activity will appear here.",
  })]);
  output.scrollTop = $("log-follow").checked ? output.scrollHeight : previousScroll;
  updateLogStatus();
}

function updateLogStatus() {
  const live = $("log-live").checked;
  $("log-status").textContent =
    `${live ? "Live · every 5s" : "Auto-refresh paused"} · ${logState.visible.length} / ${logState.entries.length} events` +
    (logState.updated ? ` · Updated ${logState.updated}` : "") +
    (logState.truncated ? " · Older events omitted" : "");
}

async function loadLogs() {
  if (logState.loading) return logState.loading;
  logState.loading = (async () => {
    try {
      const data = await get("/api/logs");
      const entries = data.entries || [];
      const snapshot = JSON.stringify(entries);
      logState.updated = new Date().toLocaleTimeString();
      logState.truncated = Boolean(data.truncated);
      if (snapshot !== logState.snapshot) {
        logState.entries = entries;
        logState.snapshot = snapshot;
        renderLogs();
      } else {
        updateLogStatus();
      }
    } catch (error) {
      $("log-status").textContent = `Refresh failed: ${error.message}. Previous events retained.`;
    }
  })();
  try {
    await logState.loading;
  } finally {
    logState.loading = null;
  }
}

async function clearLogs() {
  const confirmed = await confirmDialog({
    title: "Clear the current worker log?",
    message: "Rotated log files are kept.",
    confirmLabel: "Clear log",
    danger: true,
  });
  if (!confirmed) return;
  await logState.loading;
  await post("/api/logs/clear");
  logState.snapshot = "";
  await loadLogs();
  toast("Current log cleared", "success");
}

function downloadLogs() {
  const text = logState.visible.map((entry) =>
    `${entry.timestamp} ${entry.level} ${entry.message}${entry.details ? "\n" + entry.details.trimEnd() : ""}`).join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const link = h("a", { href: url, download: `worker-logs-${new Date().toISOString().replace(/[:.]/g, "-")}.txt` });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default {
  title: "Logs",
  subtitle: "Recent worker output.",

  init() {
    bindAction("refresh-logs", loadLogs);
    bindAction("clear-logs", clearLogs);
    $("download-logs").addEventListener("click", downloadLogs);
    $("log-search").addEventListener("input", renderLogs);
    $("log-level").addEventListener("change", renderLogs);
    $("log-follow").addEventListener("change", renderLogs);
    $("log-live").addEventListener("change", () => {
      updateLogStatus();
      if ($("log-live").checked) loadLogs();
    });
    $("log-output").addEventListener("scroll", (event) => {
      const el = event.currentTarget;
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 40) $("log-follow").checked = false;
    });
  },

  enter: loadLogs,

  /** Called by the shared poll every few seconds while this view is open. */
  tick() {
    if ($("log-live").checked) loadLogs();
  },
};
