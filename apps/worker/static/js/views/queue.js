import { get, post } from "../api.js";
import { basename, dirname, plural } from "../format.js";
import { jobCard, QUEUE_STATES } from "../jobs.js";
import { refreshQueue, store, subscribe } from "../store.js";
import { actionButton, bindAction, confirmDialog, emptyState, errorState, h, loadingState, pairPill, replaceKeepingOpen, toast } from "../ui.js";

const EMPTY = {
  pending: ["Nothing waiting", "Jobs queued by Bazarr or this console appear here."],
  processing: ["Idle", "No translation is running."],
  deferred: ["Nothing deferred", "Jobs waiting to retry after a Gemini capacity error appear here."],
  done: ["No finished jobs yet", "Completed translations appear here."],
  failed: ["No failed jobs", "Everything that ran has either finished or is still queued."],
  held: ["Nothing to approve", "With “Hold for my approval” on in Settings, jobs from Bazarr wait here until you approve them."],
  rejected: ["Nothing rejected", "Jobs you reject stay here, so Bazarr cannot queue them again. Restore one to reconsider it."],
};

// Held and rejected jobs can be many, so they come from their own endpoints instead of the polled snapshot.
const INBOX_TABS = ["held", "rejected"];
const inbox = { held: null, rejected: null };
const selected = new Set();

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
  return ["held", "processing", "pending", "failed", "deferred"].find((state) => counts[state]) || "pending";
}

// ---- Held inbox (FR-12) -------------------------------------------------

/** The series folder a job belongs to: Bazarr's series id groups episodes; a movie is grouped by its folder. */
function groupOf(job) {
  const folder = dirname(job.subtitle_path || "");
  const season = /^(season|staffel|saison|temporada|s)\s*\d+$/i.test(basename(folder));
  const seriesFolder = season ? dirname(folder) : folder;
  return { key: job.series_id ? `series:${job.series_id}` : `folder:${folder}`, title: basename(seriesFolder) || folder || "Unknown",
    seriesId: job.series_id || "" };
}

function heldMatches(job, query) {
  return !query || [job.subtitle_path, job.source_code, job.target_code, job.job_id]
    .some((value) => String(value || "").toLowerCase().includes(query));
}

async function refreshInbox() {
  for (const name of INBOX_TABS) {
    if (name !== tab) continue;
    try {
      inbox[name] = (await get(`/api/queue/${name}`)).items || [];
    } catch (error) {
      toast(error.message, "error");
    }
  }
  render();
}

async function decide(action, body, { title, message, confirmLabel, danger = false, ask = true }) {
  if (ask) {
    const confirmed = await confirmDialog({ title, message, confirmLabel, dismissLabel: "Cancel", danger });
    if (!confirmed) return;
  }
  const data = await post(`/api/queue/${action}`, body);
  selected.clear();
  toast(`${plural(data.count, "job")} ${{ approve: "approved", reject: "rejected", restore: "restored" }[action]}`, "success");
  await refreshQueue();
  await refreshInbox();
}

function approve(jobs, label) {
  const ids = jobs.map((job) => job.job_id);
  return decide("approve", { job_ids: ids }, {
    title: `Approve ${plural(ids.length, "job")}?`,
    message: `${label} Each one is translated unless its subtitle already exists. This uses model quota.`,
    confirmLabel: `Approve ${ids.length}`, ask: ids.length > 1,
  });
}

function reject(jobs, label) {
  const ids = jobs.map((job) => job.job_id);
  return decide("reject", { job_ids: ids }, {
    title: `Reject ${plural(ids.length, "job")}?`,
    message: `${label} They will not be translated, and Bazarr cannot queue them again. You can restore them from the Rejected tab.`,
    confirmLabel: `Reject ${ids.length}`, danger: true, ask: ids.length > 1,
  });
}

function heldRow(job) {
  const check = h("input", { type: "checkbox", "aria-label": `Select ${basename(job.subtitle_path)}` });
  check.checked = selected.has(job.job_id);
  check.addEventListener("change", () => {
    if (check.checked) selected.add(job.job_id); else selected.delete(job.job_id);
    render(true);
  });
  return h("article", { class: "job job-held" },
    check,
    h("div", { class: "job-main" },
      h("div", { class: "job-head" }, pairPill(job.source_code, job.target_code),
        h("span", { class: "job-name", title: job.subtitle_path, text: basename(job.subtitle_path) || job.job_id })),
      h("div", { class: "job-meta" },
        h("span", { text: `${job.source_language || job.source_code} → ${job.target_language || job.target_code}` }),
        job.cue_count ? h("span", { text: plural(job.cue_count, "cue") }) : null,
        job.created_at ? h("span", {}, "Arrived ", h("span", { dataset: { rel: job.created_at } })) : null)),
    h("div", { class: "job-actions" },
      actionButton("Approve", () => approve([job], ""), { variant: "btn-primary" }),
      actionButton("Reject", () => reject([job], ""), { variant: "btn-danger" })));
}

function heldGroups(jobs) {
  const groups = new Map();
  for (const job of jobs) {
    const group = groupOf(job);
    if (!groups.has(group.key)) groups.set(group.key, { ...group, jobs: [] });
    groups.get(group.key).jobs.push(job);
  }
  return [...groups.values()].sort((a, b) => a.title.localeCompare(b.title)).map((group) => {
    const all = h("input", { type: "checkbox", "aria-label": `Select all of ${group.title}` });
    all.checked = group.jobs.every((job) => selected.has(job.job_id));
    all.addEventListener("change", () => {
      group.jobs.forEach((job) => (all.checked ? selected.add(job.job_id) : selected.delete(job.job_id)));
      render(true);
    });
    const label = `All ${group.jobs.length} from “${group.title}”.`;
    return h("section", { class: "held-group" },
      h("div", { class: "held-group-head" }, all,
        h("span", { class: "held-title", title: group.title, text: group.title }),
        h("span", { class: "muted", text: plural(group.jobs.length, "job") }),
        actionButton("Approve all", () => approve(group.jobs, label), { variant: "btn-primary" }),
        actionButton("Reject all", () => reject(group.jobs, label), { variant: "btn-danger" })),
      group.jobs.map(heldRow));
  });
}

function rejectedRow(job) {
  return h("article", { class: "job" },
    h("div", { class: "job-main" },
      h("div", { class: "job-head" }, pairPill(job.source_code, job.target_code),
        h("span", { class: "job-name", title: job.subtitle_path, text: basename(job.subtitle_path) || job.job_id })),
      h("div", { class: "job-path mono", text: dirname(job.subtitle_path || "") })),
    h("div", { class: "job-actions" },
      actionButton("Restore", () => decide("restore", { job_ids: [job.job_id] }, { ask: false }), { iconName: "retry" })));
}

function renderInbox(list, query, force) {
  const all = inbox[tab];
  const jobs = (all || []).filter((job) => heldMatches(job, query));
  const visible = new Set(jobs.map((job) => job.job_id));
  for (const id of [...selected]) if (!visible.has(id)) selected.delete(id);  // gone or filtered out
  const chosen = jobs.filter((job) => selected.has(job.job_id));

  const held = tab === "held";
  const show = (id, on) => { document.getElementById(id).hidden = !on; };
  show("queue-approve-selected", held && chosen.length > 0);
  show("queue-reject-selected", held && chosen.length > 0);
  show("queue-approve-all", held && jobs.length > 0);
  show("queue-restore-all", tab === "rejected" && jobs.length > 0);
  document.getElementById("queue-approve-selected").textContent = `Approve selected (${chosen.length})`;
  document.getElementById("queue-reject-selected").textContent = `Reject selected (${chosen.length})`;
  document.getElementById("queue-approve-all").textContent = `Approve all (${jobs.length})`;

  const signature = JSON.stringify([tab, query, jobs, [...selected]]);
  if (!force && signature === lastSignature) return;
  lastSignature = signature;
  const footnote = document.getElementById("queue-footnote");
  footnote.hidden = true;
  if (all === null) { list.replaceChildren(loadingState("Loading…")); return; }
  const [emptyTitle, emptyText] = query ? ["No matching jobs", "Try a different filter."] : EMPTY[tab];
  replaceKeepingOpen(list, jobs.length
    ? (held ? heldGroups(jobs) : jobs.map(rejectedRow))
    : [emptyState(emptyTitle, emptyText)]);
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
  if (INBOX_TABS.includes(tab)) {
    document.getElementById("queue-retry-all").hidden = true;
    renderInbox(list, query, force);
    return;
  }
  for (const id of ["queue-approve-selected", "queue-reject-selected", "queue-approve-all", "queue-restore-all"]) {
    document.getElementById(id).hidden = true;
  }
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
    message: "Each job moves back to pending and runs again from the start.",
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
    // Each poll of the snapshot also reloads the inbox list while one of its tabs is open.
    subscribe("queue", () => (active && INBOX_TABS.includes(tab) ? refreshInbox() : render()));
    document.getElementById("queue-filter").addEventListener("input", () => render());
    bindAction("queue-retry-all", retryAll);
    const chosen = () => (inbox.held || []).filter((job) => selected.has(job.job_id));
    bindAction("queue-approve-selected", () => approve(chosen(), "The selected jobs."));
    bindAction("queue-reject-selected", () => reject(chosen(), "The selected jobs."));
    bindAction("queue-approve-all", () => approve((inbox.held || []).filter((job) => heldMatches(job, filterText())), "Everything shown."));
    bindAction("queue-restore-all", () => decide("restore", { job_ids: (inbox.rejected || []).map((job) => job.job_id) },
      { title: "Restore all rejected jobs?", message: "They return to Held and wait for your decision.", confirmLabel: "Restore all" }));
  },

  async enter(param) {
    active = true;
    if ([...QUEUE_STATES, ...INBOX_TABS].includes(param)) tab = param;
    lastSignature = "";
    render(true);
    await refreshQueue();
    if (INBOX_TABS.includes(tab)) await refreshInbox();
  },

  leave() {
    active = false;
  },
};
