import { api, get, post } from "../api.js";
import { dirname, formatBytes, formatDateTime } from "../format.js";
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
  const data = await post("/api/backups");
  toast(`Backup created: ${data.backup.name}`, "success");
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

export default {
  title: "Backups",
  subtitle: "Save and restore the console configuration.",

  init() {
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
