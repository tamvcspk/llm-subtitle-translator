import type { QueueAnswer, QueueJob, ResearchEstimate } from "../api/types";
import { lastLine, whenText } from "./activity";

/** `Research terms (3)`: how many terms two or more episodes share that the glossary and the inbox do not have yet. */
export const researchLabel = (terms: number | undefined): string => `Research terms (${(terms ?? 0).toLocaleString()})`;

export interface ResearchOff {
  reason: string;
  /** The way out is in Settings, Translation: the reason carries a link there. */
  settings: boolean;
}

/** Why the button is off (UX §6.7), or `null` when a run can be made. */
export function researchOff(estimate: Pick<ResearchEstimate, "blocked"> | undefined): ResearchOff | null {
  switch (estimate?.blocked) {
    case "no_terms":
      return { reason: "No unmapped terms found", settings: false };
    case "no_web_search":
      return { reason: "Web research requires Gemini", settings: true };
    case "budget":
      return { reason: "Research budget reached for this series", settings: true };
    default:
      return null;
  }
}

const plural = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** The rows of the Estimate box: what is looked up, what it costs and what is left of the series' budget. */
export function researchLines(estimate: ResearchEstimate): Array<[string, string]> {
  const lines: Array<[string, string]> = [
    ["Terms", estimate.asked < estimate.terms ? `${estimate.asked.toLocaleString()} of ${estimate.terms.toLocaleString()}` : estimate.terms.toLocaleString()],
    ["Model calls (est.)", `${estimate.calls.toLocaleString()} (1 search, 1 read)`],
    ["Search model", estimate.search_model],
    ["Read model", estimate.read_model],
  ];
  const { cap, used, after } = estimate.budget;
  lines.push(["Series budget", cap > 0 ? `${used.toLocaleString()} → ${after.toLocaleString()} of ${cap.toLocaleString()}` : `${used.toLocaleString()} used · no cap`]);
  if (estimate.calls_left_today !== null) lines.push(["Calls left today", estimate.calls_left_today.toLocaleString()]);
  if (estimate.paused_until !== null) lines.push(["Paused until", whenText(estimate.paused_until)]);
  return lines;
}

export type ResearchTab = "pending" | "processing" | "deferred" | "done" | "failed" | "cancelled";

/** A research job and the list of the queue it sits in (the lists are the console's own states, as `Activity` has them). */
export interface ResearchJobView {
  job: QueueJob;
  tab: ResearchTab;
}

/** The newest research job of one series and language: the one still open if there is one, else the last that ended. */
export function researchJobOf(queue: QueueAnswer | undefined, seriesId: number | null, lang: string): ResearchJobView | null {
  if (!queue || seriesId === null) return null;
  const mine = (job: QueueJob) => job.kind === "research" && job.stored_series_id === seriesId && (job.target_code ?? "") === lang;
  for (const tab of ["processing", "pending", "deferred"] as const) {
    const job = (queue[tab] ?? []).find(mine);
    if (job) return { job, tab };
  }
  const ended = (["done", "failed", "cancelled"] as const).flatMap((tab) => (queue[tab] ?? []).filter(mine).map((job) => ({ job, tab })));
  return ended.sort((a, b) => (b.job.created_at ?? 0) - (a.job.created_at ?? 0))[0] ?? null;
}

export interface ResearchStrip {
  state: "running" | "waiting" | "done" | "failed" | "stopped";
  text: string;
  /** The job is open: it can be stopped. */
  stop: boolean;
  /** What a person does next: look at what was found, or ask again. */
  next: "review" | "retry" | null;
}

const WAIT: Record<string, string> = {
  quota: "the daily quota",
  provider: "the provider",
  rate: "the search quota or the provider",
};

/** The strip under the button, in the manner of Extract facts (UX §6.7): every state says what is going on and what to do next. */
export function stripFor(job: QueueJob, tab: ResearchTab, nowMs: number = Date.now()): ResearchStrip {
  const report = (job.report ?? {}) as { added?: number; auto_accepted?: number };
  switch (tab) {
    case "processing":
      return { state: "running", text: "Looking terms up on the web…", stop: true, next: null };
    case "pending":
      return { state: "waiting", text: "Waiting in line.", stop: true, next: null };
    case "deferred": {
      const until = whenText(job.retry_at, nowMs);
      return { state: "waiting", text: `Waiting for ${WAIT[job.wait_reason ?? ""] ?? "its turn"}${until ? ` · tries again at ${until}` : ""}.`, stop: true, next: null };
    }
    case "failed":
      return { state: "failed", text: `Failed: ${lastLine(job.error) || "the research stopped"}`, stop: false, next: "retry" };
    case "cancelled":
      return { state: "stopped", text: "Stopped.", stop: false, next: "retry" };
    case "done": {
      switch (job.result) {
        case "proposed": {
          const added = report.added ?? 0;
          const applied = report.auto_accepted ?? 0;
          const by = applied > 0 ? `, ${applied.toLocaleString()} applied by themselves` : "";
          return { state: "done", text: `Done: ${plural(added, "term", "terms")} found${by}.`, stop: false, next: "review" };
        }
        case "budget-reached":
          return { state: "done", text: "Stopped: the research budget of this series is spent.", stop: false, next: null };
        case "skipped-no-web-search":
          return { state: "done", text: "Skipped: this provider has no web search.", stop: false, next: null };
        default:
          return { state: "done", text: "Done: the web had nothing new for these terms.", stop: false, next: null };
      }
    }
  }
}
