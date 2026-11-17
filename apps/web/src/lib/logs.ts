import type { LogEntry } from "../api/queries";

export const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;
export type LevelFilter = "ALL" | (typeof LEVELS)[number];

/** The worker writes `WARNING` and `CRITICAL`; a level it does not list counts as the nearest one. */
const RANK: Record<string, number> = { DEBUG: 0, INFO: 1, WARNING: 2, WARN: 2, ERROR: 3, CRITICAL: 3 };

/** That level and worse, and lines that contain the search text (in the line or in its trace). */
export function filterLogs(entries: LogEntry[], level: LevelFilter, search: string): LogEntry[] {
  const floor = level === "ALL" ? -1 : RANK[level] ?? 0;
  const needle = search.trim().toLowerCase();
  return entries.filter(
    (entry) =>
      (RANK[entry.level.toUpperCase()] ?? 1) >= floor &&
      (needle === "" || `${entry.message}\n${entry.details}`.toLowerCase().includes(needle)),
  );
}

/** `21:58:01` in the viewer's zone; the worker writes UTC with a `Z`. Empty for a line with no time. */
export function logTime(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function levelColor(level: string): string {
  switch ((RANK[level.toUpperCase()] ?? 1) as number) {
    case 3:
      return "red";
    case 2:
      return "orange";
    case 0:
      return "gray";
    default:
      return "dimmed";
  }
}
