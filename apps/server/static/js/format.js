const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export const nowSeconds = () => Date.now() / 1000;

export function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDateTime(epochSeconds) {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes) return `${minutes}m ${String(rest).padStart(2, "0")}s`;
  return `${rest}s`;
}

export function formatRelative(epochSeconds) {
  if (!epochSeconds) return "";
  const diff = Number(epochSeconds) - nowSeconds();
  const abs = Math.abs(diff);
  if (abs < 45) return diff < 0 ? "just now" : "in a moment";
  if (abs < 3600) return relative.format(Math.round(diff / 60), "minute");
  if (abs < 86400 * 2) return relative.format(Math.round(diff / 3600), "hour");
  return relative.format(Math.round(diff / 86400), "day");
}

export function basename(path) {
  return String(path || "").split(/[\\/]/).pop();
}

export function dirname(path) {
  const text = String(path || "");
  const index = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  return index > 0 ? text.slice(0, index) : "";
}

export function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}
