import type { FromEditsAnswer, InsightsAb, InsightsDay, Rate } from "../api/types";

export const DAY_CHOICES = [7, 30, 90] as const;

const DASH = "—";

/** `3.2 %`; `—` when there was nothing to divide by (no data is not 0 %). */
export function percent(rate: Rate): string {
  if (rate === null) return DASH;
  const value = rate * 100;
  return `${value === 0 ? "0" : value < 10 ? value.toFixed(1) : value.toFixed(0)} %`;
}

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : Math.round(value).toLocaleString();
}

/** `45 s`, `3 min`, `2 h 5 min`. */
export function duration(seconds: number | null): string {
  if (seconds === null) return DASH;
  if (seconds < 90) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 === 0 ? `${hours} h` : `${hours} h ${minutes % 60} min`;
}

export function millis(value: number | null): string {
  if (value === null) return DASH;
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

/** `12 of 50 today`, or `12 today, no limit` when the limit is 0. */
export function against(used: number, limit: number, unit: string): string {
  return limit > 0 ? `${used.toLocaleString()} of ${limit.toLocaleString()} ${unit}` : `${used.toLocaleString()} ${unit}, no limit`;
}

/** The daily limit that bites first: the cap the admin set, else the provider's own, else none. */
export function dailyLimit(limits: { rpd: number; max_calls_per_day: number }): number {
  const caps = [limits.max_calls_per_day, limits.rpd].filter((cap) => cap > 0);
  return caps.length === 0 ? 0 : Math.min(...caps);
}

export const OUTCOME_LABEL: Record<string, string> = {
  ok: "Answered",
  rate_limited: "Too many requests (429)",
  quota_exhausted: "Daily quota spent",
  unavailable: "Overloaded (503)",
  bad_output: "Unusable answer",
  auth_error: "Key refused",
  model_not_found: "Model not found",
  error: "Other error",
};

export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABEL[outcome] ?? outcome;
}

export const REVIEW_LABEL: Record<string, string> = {
  cps: "Read too fast",
  line_length: "Line too long",
  lines: "Too many lines",
  untranslated: "Left in the source language",
  glossary: "Locked term missed",
  missing: "Cue missing",
  empty: "Cue empty",
  tags: "Tags changed",
  count: "Cue count differs",
};

export function reviewLabel(kind: string): string {
  return REVIEW_LABEL[kind] ?? kind;
}

/** `casual · prompt p3`: which style and prompt an A/B score is for. */
export function abLabel(row: Pick<InsightsAb, "style" | "prompt_version">): string {
  const style = row.style || "(no name)";
  return row.prompt_version ? `${style} · prompt ${row.prompt_version}` : style;
}

/** The share of a day's calls bar, 0 to 100, against the busiest day of the window. */
export function barWidth(day: Pick<InsightsDay, "calls">, days: readonly Pick<InsightsDay, "calls">[]): number {
  const busiest = Math.max(0, ...days.map((d) => d.calls));
  return busiest === 0 ? 0 : Math.round((day.calls / busiest) * 100);
}

/** What looking at the corrections of a series found, in a sentence. */
export function fromEditsLine(answer: FromEditsAnswer): string {
  if (answer.cues === 0) {
    return answer.without_before > 0
      ? "Your corrections are older than this feature and do not say what the machine wrote, so there is nothing to compare."
      : "No cue of this series has been corrected yet.";
  }
  const looked = `Looked at ${answer.cues.toLocaleString()} corrected cue${answer.cues === 1 ? "" : "s"}.`;
  if (answer.patterns === 0) return `${looked} No word was changed the same way in three cues yet.`;
  const added = answer.report?.added ?? 0;
  const waiting = answer.report?.pending_already ?? 0;
  const parts = [`${answer.patterns} repeated change${answer.patterns === 1 ? "" : "s"}`];
  if (added > 0) parts.push(`${added} new suggestion${added === 1 ? "" : "s"} in Review`);
  else if (waiting > 0) parts.push(`${waiting} already waiting in Review`);
  else if (answer.proposed > 0) parts.push("already decided or already in the glossary");
  if (answer.unnamed > 0) parts.push(`${answer.unnamed} without a word of the source in common, so no term to suggest`);
  return `${looked} ${parts.join("; ")}.`;
}
