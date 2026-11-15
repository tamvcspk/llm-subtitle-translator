import { api, get, post } from "../api.js";
import { dirname, formatBytes, formatDateTime, plural } from "../format.js";
import { refreshStatus } from "../store.js";
import { bindAction, confirmDialog, emptyState, errorState, h, icon, loadingState, toast } from "../ui.js";

function backupRow(item) {
  return h("div", { class: "row row-backup" },
    h("div", { class: "row-main" },
      h("div", { class: "row-title", text: item.name }),
      h("div", { class: "row-path mono", text: dirname(item.path) })),
    h("div", { class: "muted", text: formatBytes(item.size) }),
    h("div", { class: "muted", text: formatDateTime(item.created_at) }),
    h("div", { class: "row-action" },
      h("a", {
        class: "btn btn-sm",
        href: `/api/backups/download?name=${encodeURIComponent(item.name)}`,
        download: item.name,
      }, icon("download"), "Download")));
}

async function loadBackups() {
  const list = document.getElementById("backup-list");
  if (!list.childElementCount) list.replaceChildren(loadingState("Loading backups…"));
  try {
    const data = await get("/api/backups");
    const items = data.items || [];
    list.replaceChildren(...(items.length
      ? items.map(backupRow)
      : [emptyState("No backups yet", "Create one before changing settings you might want back.", "archive")]));
  } catch (error) {
    list.replaceChildren(errorState(error.message, loadBackups));
  }
}

async function createBackup() {
  const includeKeys = document.getElementById("backup-include-keys").checked;
  const data = await post("/api/backups", { include_keys: includeKeys });
  document.getElementById("backup-include-keys").checked = false;
  toast(`Backup created: ${data.backup.name}${includeKeys ? " (with API keys)" : ""}`, "success");
  await loadBackups();
}

async function importBackup() {
  const input = document.getElementById("backup-file-input");
  const file = input.files && input.files[0];
  if (!file) return;
  const confirmed = await confirmDialog({
    title: "Import this backup?",
    message: `Current settings and post-processing targets will be replaced with the contents of ${file.name}. A pre-import backup is created first.`,
    confirmLabel: "Import",
    danger: true,
  });
  if (!confirmed) return;
  const payload = await api("/api/backups/import", {
    method: "POST",
    body: file,
    headers: { "Content-Type": "application/zip" },
  });
  input.value = "";
  input.dispatchEvent(new Event("change"));
  toast(`Backup imported. Pre-import backup: ${payload.pre_import_backup.name}`, "success");
  refreshStatus().catch(() => {});
  await loadBackups();
}

// ---- Clean-up (FR-13) ----------------------------------------------------------------------------

const CLEANUP_GROUPS = {
  jobs_done: ["Finished jobs", (w) => `older than ${plural(w, "day")}`],
  jobs_failed: ["Failed jobs", (w) => `older than ${plural(w, "day")}`],
  versions: ["Older subtitle versions", (w) => `beyond the newest ${w} (the current and the first are kept)`],
  glossary: ["Glossary backups", (w) => `beyond the newest ${w} per series and language`],
  temp: ["Leftover temporary files", () => "older than a day, in the queue and data folders"],
};

function cleanupAmount(group) {
  const parts = [];
  if (group.files) parts.push(plural(group.files, "file"));
  if (group.rows) parts.push(plural(group.rows, "row"));
  if (!parts.length) return "nothing to remove";
  return group.bytes ? `${parts.join(", ")} · ${formatBytes(group.bytes)}` : parts.join(", ");
}

function cleanupRow(group, dryRun) {
  const [title, describe] = CLEANUP_GROUPS[group.name] || [group.name, () => ""];
  const detail = group.error ? `failed: ${group.error}` : group.enabled
    ? describe(group.window)
    : "switched off in Settings";
  return h("div", { class: "row row-backup" },
    h("div", { class: "row-main" },
      h("div", { class: "row-title", text: title }),
      h("div", { class: "row-path", text: detail })),
    h("div", { class: "muted", text: group.enabled ? `${dryRun ? "Would remove" : "Removed"}: ${cleanupAmount(group)}` : "" }));
}

function renderCleanup(report) {
  const list = document.getElementById("cleanup-report");
  const nodes = report.groups.map((group) => cleanupRow(group, report.dry_run));
  if (report.media_temp.length) {
    nodes.push(h("div", { class: "row row-backup" },
      h("div", { class: "row-main" },
        h("div", { class: "row-title", text: "Leftovers beside your media" }),
        h("div", { class: "row-path mono", text: report.media_temp.join("\n") }),
        h("div", { class: "hint hint-warn", text: "Listed only: the clean-up never deletes anything in a media folder. Delete them yourself if you want them gone." }))));
  }
  list.replaceChildren(...nodes);
}

async function previewCleanup() {
  renderCleanup(await post("/api/cleanup", { dry_run: true }));
}

async function runCleanup() {
  const preview = await post("/api/cleanup", { dry_run: true });
  renderCleanup(preview);
  const { files, rows, bytes } = preview.total;
  if (!files && !rows) {
    toast("Nothing to clean up", "success");
    return;
  }
  const confirmed = await confirmDialog({
    title: "Clean up now?",
    message: `This deletes ${plural(files, "file")} (${formatBytes(bytes)}) and ${plural(rows, "database row")} listed above. Your media folders are not touched.`,
    confirmLabel: "Clean up",
    danger: true,
  });
  if (!confirmed) return;
  const done = await post("/api/cleanup", { dry_run: false });
  renderCleanup(done);
  toast(`Cleaned up ${plural(done.total.files, "file")} and ${plural(done.total.rows, "row")}`, "success");
}

export default {
  title: "Backups",
  subtitle: "Save and restore the console configuration, and clean up old files.",

  init() {
    bindAction("cleanup-preview", previewCleanup);
    bindAction("cleanup-run", runCleanup);
    bindAction("create-backup", createBackup);
    const importButton = bindAction("import-backup", importBackup);
    const input = document.getElementById("backup-file-input");
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      document.getElementById("backup-file-name").textContent = file ? file.name : "Choose a backup .zip to import…";
      importButton.disabled = !file;
    });
  },

  enter: loadBackups,
};
