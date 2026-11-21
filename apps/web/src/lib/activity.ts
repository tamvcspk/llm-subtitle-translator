import type { ModelLayers, ProviderHealth, QueueAnswer, QueueCounts, QueueJob } from "../api/types";
import { clock } from "./format";

export type JobTab = "requested" | "running" | "waiting" | "failed" | "done" | "cancelled";

/** The tabs of Activity use the labels of UX §3, never the internal state names (`pending`, `processing`, `deferred`). */
export const JOB_TABS: Array<{ value: JobTab; label: string; state: keyof QueueCounts }> = [
  { value: "requested", label: "Requested", state: "pending" },
  { value: "running", label: "Running", state: "processing" },
  { value: "waiting", label: "Waiting", state: "deferred" },
  { value: "failed", label: "Failed", state: "failed" },
  { value: "done", label: "Done", state: "done" },
  // One tab for a job cancelled before it started and one stopped while it ran: the worker keeps one state for both.
  { value: "cancelled", label: "Cancelled", state: "cancelled" },
];

export const isJobTab = (value: string | null): value is JobTab => JOB_TABS.some((tab) => tab.value === value);

export function jobsOf(queue: QueueAnswer, tab: JobTab): QueueJob[] {
  const state = JOB_TABS.find((candidate) => candidate.value === tab)?.state ?? "pending";
  return queue[state] ?? [];
}

/** What to look at first when nobody has chosen: what is running, then what is in line, then what needs a person. */
export function firstTab(counts: QueueCounts): JobTab {
  if (counts.processing > 0) return "running";
  if (counts.pending > 0) return "requested";
  if (counts.failed > 0) return "failed";
  if (counts.deferred > 0) return "waiting";
  return "done";
}

const baseName = (path: string | undefined) => (path ?? "").split(/[\\/]/).pop() ?? "";

/** Jobs that read and do not translate: they have no subtitle to write and no bundle to export. */
export const readsOnly = (job: Pick<QueueJob, "kind">): boolean => job.kind === "extract" || job.kind === "suggest";

/**
 * `Arcane · S01E04 · vi`; a job with no unit (an older queue file) is its subtitle's file name. A job that reads is named
 * for what it reads: `Extract facts · Arcane · S01E04`, `Look for proposals · Arcane · vi`.
 */
export function jobName(job: QueueJob): string {
  if (job.kind === "extract") return ["Extract facts", job.title, job.label].filter(Boolean).join(" · ");
  if (job.kind === "suggest") return ["Look for proposals", job.title, job.target_code].filter(Boolean).join(" · ");
  const parts = [job.title, job.label].filter(Boolean);
  const name = parts.length > 0 ? parts.join(" · ") : baseName(job.subtitle_path) || job.job_id;
  return job.target_code ? `${name} · ${job.target_code}` : name;
}

/** The last non-empty line of an error: the one that says what went wrong (the rest is the trace). */
export function lastLine(text: string | undefined): string {
  const lines = (text ?? "").split(/\r?\n/).filter((line) => line.trim() !== "");
  return lines.at(-1)?.trim() ?? "";
}

/** `00:00` for today, a short date and time for any other day. Nothing for a time that is not a time. */
export function whenText(unixSeconds: number | null | undefined, nowMs: number = Date.now()): string {
  if (!unixSeconds || unixSeconds <= 0 || unixSeconds >= 1e12) return "";
  const date = new Date(unixSeconds * 1000);
  return date.toDateString() === new Date(nowMs).toDateString() ? clock(unixSeconds) : date.toLocaleString([], { dateStyle: "short", timeStyle: "short" });
}

const WAIT_TEXT: Record<string, string> = {
  quota: "quota",
  provider: "provider",
  context: "context",
  settle: "settling",
  rate: "provider busy",
};

/** What a reading job's result says, in words (the worker's result strings are not for people). */
const READ_RESULT: Record<string, string> = {
  extracted: "facts read",
  cached: "the facts were already there",
  stored: "kept the facts it had",
  "skipped-no-source": "no source subtitle to read",
};

/** `manual · fill`, `policy · variant`: who asked and how. */
export function originLine(job: QueueJob): string {
  return [job.origin, job.trigger === "variant" ? "variant" : job.mode].filter(Boolean).join(" · ");
}

/** The second line of a job: origin and mode, then what it is doing, or waiting for, or why it failed. */
export function jobStatus(job: QueueJob, tab: JobTab, nowMs: number = Date.now()): string {
  const parts: string[] = [originLine(job)];
  switch (tab) {
    case "requested":
      parts.push("Requested");
      break;
    case "running":
      parts.push(job.progress_total ? `translating ${job.progress_done ?? 0}/${job.progress_total}` : job.stage || "working");
      break;
    case "waiting": {
      const reason = job.wait_reason ?? "";
      const needs = reason === "context" && job.missing_units && job.missing_units.length > 0 ? ` (needs ${job.missing_units.join(", ")})` : "";
      parts.push(`Waiting · ${WAIT_TEXT[reason] ?? (reason || "…")}${needs}`);
      const until = whenText(job.retry_at, nowMs);
      if (until) parts.push(`retry at ${until}`);
      break;
    }
    case "failed":
      parts.push("Failed", lastLine(job.error));
      break;
    case "cancelled":
      parts.push("Cancelled");
      break;
    case "done":
      parts.push((readsOnly(job) ? READ_RESULT[job.result ?? ""] : undefined) ?? (job.result || "done"));
      if (job.output_path) parts.push(`wrote ${baseName(job.output_path)}`);
      break;
  }
  if (job.attempts) parts.push(`restarts ${job.attempts}`);
  return parts.filter(Boolean).join(" · ");
}

export function providerLine(provider: ProviderHealth): { head: string; detail: string } {
  const head = `${provider.state.replace("_", " ")} · ${provider.usable ? "usable" : "not usable"} · failures ${provider.failures}`;
  const until = provider.until ? whenText(provider.until) : "";
  const detail = [provider.reason, until ? `until ${until}` : ""].filter(Boolean).join(" · ");
  return { head, detail };
}

export interface LayerRow {
  model: string;
  done: number;
  failed: number;
}

export function layerRows(layers: ModelLayers | undefined): LayerRow[] {
  return Object.entries(layers?.by_model ?? {}).map(([model, counts]) => ({ model, ...counts }));
}

/** "last 30 days" or "all time": how far back a count reaches (`0` means the kind of job is never deleted). */
export function reach(days: number): string {
  return days > 0 ? `last ${days} days` : "all time";
}
