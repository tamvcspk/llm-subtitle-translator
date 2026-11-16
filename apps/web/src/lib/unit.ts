import type { UnitDetail, UnitTarget } from "../api/types";
import { ago } from "./format";
import { episodeCode } from "./episodes";

export const EPISODE_TABS = [
  { value: "subtitles", label: "Subtitles" },
  { value: "corrections", label: "Corrections" },
  { value: "retranslate", label: "Retranslate" },
  { value: "history", label: "History" },
  { value: "context", label: "Context" },
  { value: "style", label: "Style" },
] as const;

/** A movie has no story memory, so no Context tab (wireframe 3). */
export const MOVIE_TABS = EPISODE_TABS.filter((tab) => tab.value !== "context");

export function unitTitle(unit: UnitDetail): string {
  return unit.series ? `${episodeCode(unit.season, unit.episode)} · ${unit.title}` : unit.title;
}

/** "Edited", "Translated · Stale": what the person sees first about the file of one language. */
export function targetStatus(target: UnitTarget): string {
  if (target.removed) return "Removed";
  if (target.status === "untranslated") return target.exists ? "External" : "Not translated";
  const base = target.trigger === "cue_edit" ? "Edited" : "Translated";
  if (target.status === "stale") return `${base} · Stale`;
  if (target.status === "incomplete") return `${base} · Incomplete`;
  return base;
}

/** `Current v3 · cue_edit · gemini-3.1-flash-lite · 2d ago`, or null while there is no version. */
export function versionLine(target: UnitTarget, nowMs: number = Date.now()): string | null {
  if (target.version === null) return null;
  return [`Current v${target.version}`, target.trigger, target.model, target.updated_at ? ago(target.updated_at, nowMs) : ""]
    .filter(Boolean)
    .join(" · ");
}

/** `00:14:20,100`, the way a subtitle file writes a time. */
export function formatMs(ms: number): string {
  const total = Math.max(0, Math.round(ms));
  const pad = (n: number, width = 2) => String(n).padStart(width, "0");
  const hours = Math.floor(total / 3_600_000);
  const minutes = Math.floor((total % 3_600_000) / 60_000);
  const seconds = Math.floor((total % 60_000) / 1000);
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)},${pad(total % 1000, 3)}`;
}

/** `Jump to` takes `23:10`, `1:23:10` or `00:23:10.5`; anything else is refused before it is sent. */
export function isPlayerTime(text: string): boolean {
  return /^\d{1,2}(:\d{2}){1,2}([.,]\d{1,3})?$/.test(text.trim());
}
