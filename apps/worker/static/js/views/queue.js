import { post } from "../api.js";
import { plural } from "../format.js";
import { jobCard, QUEUE_STATES } from "../jobs.js";
import { refreshQueue, store, subscribe } from "../store.js";
import { actionButton, bindAction, confirmDialog, emptyState, errorState, loadingState, replaceKeepingOpen, toast } from "../ui.js";

const EMPTY = {
  pending: ["Nothing waiting", "Jobs queued by Bazarr or this console appear here."],
  processing: ["Idle", "No translation is running."],
  deferred: ["Nothing deferred", "Jobs waiting to retry after a Gemini capacity error appear here."],
  done: ["No finished jobs yet", "Completed translations appear here."],
  failed: ["No failed jobs", "Everything that ran has either finished or is still queued."],
};

let active = false;
let tab = null;
let lastSignature = "";

function filterText() {
  return document.getElementById("queue-filter").value.trim().toLowerCase();
}

function matches(job, query) {
  if (!query) return true;
  return [job.subtitle_path, job.output_path, job.source_code, job.target_code, job.job_id, job.error]
    .some((value) => String(value || "").toLowerCase().includes(query));
}

async function retryJob(job) {
  const data = await post("/api/queue/retry", { job_id: job.job_id });
  if (!data.ok) throw new Error("This job could not be retried; it may already be queued.");
  toast("Job moved back to pending", "success");
  await refreshQueue();
}

async function cancelJob(job, state) {
  const confirmed = await confirmDialog({
    title: state === "failed" ? "Cancel this failed translation?" : `Cancel this ${state} translation?`,
    message: "It will be removed from the queue and will not be retried.",
    confirmLabel: "Cancel job",
    dismissLabel: "Keep job",
    danger: true,
  });
  if (!confirmed) return;
  if (state === "failed") await post("/api/queue/cancel", { job_id: job.job_id });
  else await post("/api/queue/delete", { state, job_id: job.job_id });
  toast("Job cancelled", "success");
  await refreshQueue();
}

function actionsFor(job, state) {
  if (state === "failed") {
    return [
      actionButton("Retry", () => retryJob(job), { iconName: "retry" }),
      actionButton("Cancel", () => cancelJob(job, state), { variant: "btn-danger" }),
    ];
  }
  if (state === "pending" || state === "deferred") {
    return [actionButton("Cancel", () => cancelJob(job, state), { variant: "btn-danger" })];
  }
  return [];
}

function pickDefaultTab(snapshot) {
  const counts = snapshot?.counts || {};
  return ["processing", "pending", "failed", "deferred"].find((state) => counts[state]) || "pending";
}

function render(force = false) {
  if (!active) return;
  const list = document.getElementById("queue-list");
  const snapshot = store.queue;
  if (!snapshot) {
    list.replaceChildren(store.queueError
      ? errorState(store.queueError.message, () => refreshQueue())
      : loadingState("Loading queue…"));
    return;
  }

  if (!tab) {
    tab = pickDefaultTab(snapshot);
    history.replaceState(null, "", `#queue/${tab}`);
  }
  const counts = snapshot.counts || {};
  document.querySelectorAll("#view-queue [data-count]").forEach((el) => {
    el.textContent = counts[el.dataset.count] || 0;
    el.classList.toggle("tab-count-alert", el.dataset.count === "failed" && counts.failed > 0);
  });
  document.querySelectorAll("#view-queue .tab").forEach((el) => {
    el.setAttribute("aria-current", el.dataset.state === tab ? "page" : "false");
  });

  const query = filterText();
  const jobs = (snapshot[tab] || []).filter((job) => matches(job, query));
  document.getElementById("queue-retry-all").hidden = !(tab === "failed" && jobs.length);

  // Polling returns the same data most of the time; skip identical redraws so
  // buttons and expanded errors stay put. Timers update on their own.
  const signature = JSON.stringify([tab, query, snapshot[tab]]);
  if (!force && signature === lastSignature) return;
  lastSignature = signature;

  const [emptyTitle, emptyText] = query ? ["No matching jobs", "Try a different filter."] : EMPTY[tab];
  replaceKeepingOpen(list, jobs.length
    ? jobs.map((job) => jobCard(job, tab, { actions: actionsFor(job, tab) }))
    : [emptyState(emptyTitle, emptyText)]);

  const shown = (snapshot[tab] || []).length;
  const footnote = document.getElementById("queue-footnote");
  footnote.hidden = !(counts[tab] > shown);
  footnote.textContent = `Showing the latest ${shown} of ${counts[tab]} jobs.`;
}

async function retryAll() {
  const jobs = (store.queue?.failed || []).filter((job) => matches(job, filterText()));
  const confirmed = await confirmDialog({
    title: `Retry ${plural(jobs.length, "failed job")}?`,
    message: "Each job moves back to pending and runs again from the start or its saved partial output.",
    confirmLabel: "Retry all",
  });
  if (!confirmed) return;
  let moved = 0;
  try {
    for (const job of jobs) {
      const data = await post("/api/queue/retry", { job_id: job.job_id });
      if (data.ok) moved += 1;
    }
  } finally {
    if (moved) toast(`${plural(moved, "job")} moved back to pending`, "success");
    await refreshQueue();
  }
}

export default {
  title: "Queue",
  subtitle: "Translation jobs by state. Updates every few seconds.",

  init() {
    subscribe("queue", () => render());
    document.getElementById("queue-filter").addEventListener("input", () => render());
    bindAction("queue-retry-all", retryAll);
  },

  async enter(param) {
    active = true;
    if (QUEUE_STATES.includes(param)) tab = param;
    render(true);
    await refreshQueue();
  },

  leave() {
    active = false;
  },
};
