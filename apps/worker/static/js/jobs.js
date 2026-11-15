// Rendering for translation jobs, shared by the dashboard and the queue view.
import { basename, dirname, formatDateTime } from "./format.js";
import { h, icon, pairPill } from "./ui.js";

export const QUEUE_STATES = ["pending", "processing", "deferred", "done", "failed"];

export const STATE_LABELS = {
  pending: "Pending",
  processing: "Translating",
  deferred: "Waiting",
  done: "Done",
  failed: "Failed",
};

function progressBlock(job) {
  const since = job.started_at || job.created_at;
  const facts = [];
  if (job.progress_total) facts.push(`${job.progress_done || 0} of ${job.progress_total} cues translated`);
  return h("div", { class: "progress" },
    h("div", { class: "progress-track" }, h("div", { class: "progress-bar" })),
    h("div", { class: "progress-meta" },
      h("strong", { text: job.stage || "Working" }),
      since && h("span", { class: "muted" }, "Running for ", h("span", { dataset: { since } }))),
    h("div", { class: "muted" }, facts.length ? facts.join(" · ") : "Waiting for Gemini response…"));
}

// A job waits for something, and says what (FR-15.8): a wait is never a failure.
export const WAIT_LABELS = {
  quota: "Waiting · daily quota spent",
  provider: "Waiting · provider (key, model or breaker)",
  context: "Waiting · earlier episodes' facts",
  settle: "Waiting · source subtitle changed a moment ago",
  rate: "Waiting · provider busy, trying again",
};

function metaLine(job, state) {
  const parts = [];
  if (state === "deferred") {
    parts.push(h("span", { class: "meta-strong" }, icon("clock"), WAIT_LABELS[job.wait_reason] || "Waiting"));
    const until = Number(job.retry_at);
    if (until > 0 && until < 1e17) parts.push(h("span", { text: `until ${formatDateTime(until)}` }));
    if (job.wait_reason === "context" && Array.isArray(job.missing_units) && job.missing_units.length)
      parts.push(h("span", { text: `missing: ${job.missing_units.join(", ")}` }));
  }
  if (job.attempts) parts.push(h("span", { text: `Restarted after a crash: ${job.attempts}` }));
  if (state === "done" && job.output_path) parts.push(h("span", { text: `Wrote ${basename(job.output_path)}` }));
  if (job.created_at) parts.push(h("span", {}, "Queued ", h("span", { dataset: { rel: job.created_at } })));
  return parts.length ? h("div", { class: "job-meta" }, parts) : null;
}

function errorBlock(job) {
  if (!job.error) return null;
  const text = String(job.error).trim();
  const headline = text.split(/\r?\n/).filter(Boolean).pop() || text;
  return h("details", { class: "job-error", dataset: { key: job.job_id } },
    h("summary", {}, icon("alert"), h("span", { text: headline })),
    h("pre", { text }));
}

/**
 * One job row. `actions` is a list of prebuilt buttons; `compact` hides the
 * directory and error body for dashboard summaries.
 */
export function jobCard(job, state, { actions = [], compact = false } = {}) {
  const path = job.subtitle_path || "";
  return h("article", { class: `job job-${state}${compact ? " job-compact" : ""}` },
    h("div", { class: "job-main" },
      h("div", { class: "job-head" },
        pairPill(job.source_code, job.target_code),
        h("span", { class: "job-name", title: path || job.job_id, text: basename(path) || job.job_id })),
      !compact && dirname(path) && h("div", { class: "job-path mono", text: dirname(path) }),
      state === "processing" && progressBlock(job),
      !compact && metaLine(job, state),
      compact
        ? job.error && h("div", { class: "job-error-line", text: String(job.error).trim().split(/\r?\n/).filter(Boolean).pop() })
        : errorBlock(job)),
    actions.length ? h("div", { class: "job-actions" }, actions) : null);
}
