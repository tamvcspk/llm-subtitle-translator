// Library: everything Bazarr lists (series, movies, episodes) and what each language of each item is doing.
// `#library` lists series and movies; `#library/<catalog id>` lists one series' episodes; `#library/movies` the movies.
// The state of an item is read on every load, never stored: "Missing · Requested", "Translated · Stale", ... (FR-14.5).
import { get, post, api } from "../api.js";
import { formatDateTime, plural } from "../format.js";
import { refreshQueue } from "../store.js";
import {
  actionButton, bindAction, confirmDialog, crumbs, emptyState, errorState, h, icon, loadingState, setHeading, toast,
} from "../ui.js";

const $ = (id) => document.getElementById(id);

const FILTERS = [
  ["", "All"], ["missing", "Missing"], ["requested", "Requested"], ["failed", "Failed"], ["ignored", "Ignored"],
];
const POLICY_LABELS = { "": "Follow the global setting", on: "Always on", off: "Always off" };

let filter = "";
let target = null; // null: the overview; "movies"; or a catalog id
let loadId = 0;
let data = null;
const selected = new Set();

/** Which tone a label gets: red for a failure, amber for what still needs a decision, green for what is done. */
function tone(label) {
  if (label.job === "failed") return "danger";
  if (label.ignored || label.content === "external" || label.content === "outside") return "muted";
  if (label.content === "missing" || label.stale) return "warn";
  return "ok";
}

function labelPill(label) {
  return h("span", { class: `status status-${tone(label)}`, title: `${label.lang}: ${label.text}` },
    h("span", { class: "dot" }), h("span", { class: "pill-lang", text: label.lang }), " ", label.text);
}

function matchesFilter(item) {
  if (!filter) return true;
  return item.targets.some((label) => ({
    missing: label.content === "missing" && !label.ignored,
    requested: ["requested", "running", "waiting"].includes(label.job),
    failed: label.job === "failed",
    ignored: label.ignored,
  })[filter]);
}

function statusBar(status) {
  const age = status.synced_at
    ? h("span", {}, "Bazarr was read ", h("span", { dataset: { rel: status.synced_at } }), ` (${formatDateTime(status.synced_at)})`)
    : h("span", { text: "Bazarr has not been read yet." });
  const bar = h("div", { class: "series-tools lib-status" }, age,
    h("span", { class: "muted", text: status.auto_request ? "Auto-request is on for everything not overridden." : "Auto-request is off: nothing is translated until you ask." }),
    actionButton("Sync now", syncNow, { iconName: "refresh" }));
  if (status.warning) {
    bar.append(h("div", { class: "banner banner-warn lib-warning", role: "status" },
      h("span", { class: "banner-icon" }, icon("alert")), h("div", {}, h("strong", { text: "The catalog is out of date." }), h("p", { text: status.warning }))));
  }
  return bar;
}

async function syncNow() {
  const status = await post("/api/catalog/sync");
  toast(status.warning ? "Bazarr could not be read; the catalog was kept as it was" : "Library updated from Bazarr",
    status.warning ? "error" : "success");
  if (status.auto_requested?.created) toast(`${plural(status.auto_requested.created, "translation")} requested by the policy`, "info");
  await load(true);
  refreshQueue().catch(() => {});
}

// ---- Overview -------------------------------------------------------------------------------

function chips(counts, langs) {
  return langs.filter((lang) => counts[lang]).map((lang) =>
    h("span", { class: "chip chip-stale", title: `Bazarr lists ${lang} as missing for ${plural(counts[lang], "item")}`, text: `${counts[lang]} ${lang} missing` }));
}

function overviewRow(href, title, subtitle, middle, badges) {
  return h("a", { class: "row row-link", href },
    h("div", { class: "row-main" }, h("div", { class: "row-title", text: title }), subtitle && h("div", { class: "row-path", text: subtitle })),
    h("div", { class: "row-langs" }, middle),
    h("div", { class: "row-action" }, badges, icon("chevron")));
}

function showOverview(page) {
  setHeading("Library", "Everything Bazarr lists, and what each language of it is doing.");
  crumbs($("library-crumbs"), [["Library"]]);
  const query = $("library-search").value.trim().toLowerCase();
  const series = page.series.filter((item) => (!query || item.title.toLowerCase().includes(query))
    && (!filter || ({ missing: Object.values(item.missing).some(Boolean), requested: Object.keys(item.open_jobs).length > 0 })[filter] !== false));
  const list = $("library-list");
  const rows = [statusBar(page.status)];
  if (!page.status.items && !page.status.synced_at) {
    rows.push(emptyState("Nothing read from Bazarr yet", "Set Bazarr's address and API key in Settings, then press Sync now.", "library"));
  } else if (!series.length && !page.movies.count) {
    rows.push(emptyState("Nothing here", query || filter ? "No series match." : "Bazarr lists no series or movies.", "library"));
  } else {
    if (series.length) {
      rows.push(h("div", { class: "group-title", text: "Series" }));
      for (const item of series) {
        const open = item.open_jobs;
        const badges = [
          open.requested && h("span", { class: "chip", text: `${open.requested} requested` }),
          open.running && h("span", { class: "chip chip-current", text: "translating" }),
          open.waiting && h("span", { class: "chip", text: `${open.waiting} waiting` }),
          item.auto_request && h("span", { class: "chip", title: "Auto-request for this series", text: `auto ${item.auto_request}` }),
        ];
        rows.push(overviewRow(`#library/${item.id}`, item.title || item.key, plural(item.episodes, "episode"), chips(item.missing, page.languages), badges));
      }
    }
    if (page.movies.count && !query) {
      rows.push(h("div", { class: "group-title", text: "Movies" }),
        overviewRow("#library/movies", "Movies", plural(page.movies.count, "movie"), chips(page.movies.missing, page.languages), null));
    }
  }
  list.replaceChildren(...rows);
}

// ---- A series or the movies -------------------------------------------------------------------------

function itemCode(item) {
  if (item.kind === "movie") return item.title || "Movie";
  const code = `S${String(item.season ?? 0).padStart(2, "0")}E${String(item.episode ?? 0).padStart(2, "0")}`;
  return `${code}${item.title ? ` · ${item.title}` : ""}`;
}

/** What asking for these items would do, in words the admin can confirm (FR-15.5). */
function estimateText(result) {
  const e = result.estimate;
  const parts = [`${plural(e.jobs, "job")}`, `${plural(e.calls, "model call")} (about)`, `${plural(e.cues, "cue")}`];
  if (e.calls_left_today != null) parts.push(`${plural(e.calls_left_today, "call")} left today`);
  if (e.minutes != null) parts.push(`about ${e.minutes} min at the recent pace`);
  if (e.paused_until) parts.push(`the daily quota is spent until ${formatDateTime(e.paused_until)}`);
  const skipped = Object.entries(result.skipped_by_reason).map(([reason, n]) => `${n} ${reason}`);
  return `${parts.join(" · ")}.${skipped.length ? ` Left out: ${skipped.join("; ")}.` : ""} Story-memory extraction, if on, adds calls.`;
}

async function request(body, { mode = "fill", label }) {
  const preview = await post("/api/jobs", { ...body, mode, estimate_only: true });
  if (!preview.planned_total) {
    const why = Object.keys(preview.skipped_by_reason).join("; ");
    toast(why ? `Nothing to request: ${why}` : "Nothing to request", "info");
    return;
  }
  if (preview.planned_total > 1) {
    const confirmed = await confirmDialog({
      title: `${mode === "replace" ? "Retranslate" : "Translate"} ${label}?`,
      message: estimateText(preview),
      confirmLabel: mode === "replace" ? "Retranslate" : "Request",
    });
    if (!confirmed) return;
  }
  const made = await post("/api/jobs", { ...body, mode });
  toast(`${plural(made.count + (made.merged || 0), "job")} requested`, "success");
  selected.clear();
  await load(true);
  refreshQueue().catch(() => {});
}

async function decide(path, ids, text) {
  await post(path, { items: ids });
  toast(text, "success");
  selected.clear();
  await load(true);
}

function itemRow(item) {
  const check = h("input", { type: "checkbox", "aria-label": `Select ${itemCode(item)}` });
  check.checked = selected.has(item.id);
  check.addEventListener("change", () => {
    if (check.checked) selected.add(item.id); else selected.delete(item.id);
    renderSelection();
  });
  // The episode page works on what this worker translated; an item without such a translation has no page yet.
  const made = item.targets.find((label) => ["translated", "edited"].includes(label.content));
  const link = item.unit_id && made
    ? h("a", { class: "link", href: `#episode/${item.unit_id}/${made.lang}`, text: itemCode(item) })
    : h("span", { text: itemCode(item) });
  return h("div", { class: `row row-select${item.gone ? " row-gone" : ""}` },
    check,
    h("div", { class: "row-main" }, h("div", { class: "row-title" }, link),
      !item.has_source && h("div", { class: "row-path", text: "No source subtitle in a configured language" }),
      item.gone && h("div", { class: "row-path", text: "Bazarr no longer lists this item" })),
    h("div", { class: "row-langs" }, item.targets.map(labelPill)),
    h("div", { class: "row-action" }, actionButton("Request", () => request({ items: [item.id] }, { label: itemCode(item) }), { iconName: "plus" })));
}

function renderSelection() {
  const count = selected.size;
  for (const id of ["library-request", "library-retranslate", "library-ignore", "library-unignore"]) $(id).hidden = count === 0;
  $("library-selected").textContent = count ? `${count} selected` : "";
}

function showItems(page) {
  const series = page.series;
  const title = series ? (series.title || series.key) : "Movies";
  setHeading(title, series ? "Episodes, and what each language of them is doing." : "Movies, and what each language of them is doing.");
  crumbs($("library-crumbs"), [["Library", "#library"], [title]]);
  const query = $("library-search").value.trim().toLowerCase();
  const items = page.items.filter((item) => matchesFilter(item) && (!query || itemCode(item).toLowerCase().includes(query)));
  const visible = new Set(items.map((item) => item.id));
  for (const id of [...selected]) if (!visible.has(id)) selected.delete(id);

  const rows = [statusBar(page.status)];
  if (series) {
    const policy = h("select", { id: "series-policy", class: "input input-sm", "aria-label": "Auto-request for this series" },
      Object.entries(POLICY_LABELS).map(([value, text]) => h("option", { value, text: `Auto-request: ${text}` })));
    policy.value = series.auto_request || "";
    policy.addEventListener("change", async () => {
      await api(`/api/catalog/series/${series.id}/policy`, { method: "PUT", body: { auto_request: policy.value } });
      toast("Auto-request for this series saved", "success");
    });
    const glossary = series.series_id
      ? page.languages.map((lang) => h("a", { class: "btn btn-sm", href: `#glossary/${series.series_id}/${lang}` }, icon("glossary"), `Glossary · ${lang}`))
      : [];
    rows.push(h("div", { class: "series-tools" }, policy, glossary,
      actionButton("Request all missing", () => request({ series: series.id }, { label: `the whole of “${title}”` })),
    ));
  }
  if (!items.length) {
    rows.push(filter || query ? emptyState("No matching items", "Try another filter.") : emptyState("Nothing listed", "Bazarr lists nothing here.", "library"));
  } else {
    const seasons = new Map();
    for (const item of items) {
      const key = item.kind === "movie" ? -1 : (item.season ?? 0);
      if (!seasons.has(key)) seasons.set(key, []);
      seasons.get(key).push(item);
    }
    for (const [season, group] of seasons) {
      if (season !== -1) {
        rows.push(h("div", { class: "group-title season-title" }, season === 0 ? "Specials" : `Season ${season}`,
          series && actionButton(`Request season`, () => request({ series: series.id, season }, { label: season === 0 ? "the specials" : `season ${season}` }), { iconName: "plus" })));
      }
      rows.push(...group.map(itemRow));
    }
  }
  $("library-list").replaceChildren(...rows);
  renderSelection();
}

// ---- Loading -------------------------------------------------------------------------------------

async function load(force = false) {
  const token = ++loadId;
  const list = $("library-list");
  if (force || !data) list.children.length || list.replaceChildren(loadingState("Loading library…"));
  try {
    const path = target === null ? "/api/catalog" : target === "movies" ? "/api/catalog/movies" : `/api/catalog/series/${target}`;
    const page = await get(path);
    if (token !== loadId) return;
    data = page;
    render();
  } catch (error) {
    if (token === loadId) list.replaceChildren(errorState(error.message, () => load(true)));
  }
}

function render() {
  if (!data) return;
  $("library-selection-tools").hidden = target === null;
  if (target === null) showOverview(data); else showItems(data);
}

function setFilter(value) {
  filter = value;
  $("library-tabs").querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.filter === filter) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  render();
}

const chosen = () => [...selected];

export default {
  title: "Library",
  subtitle: "Everything Bazarr lists, and what each language of it is doing.",

  init() {
    $("library-tabs").replaceChildren(...FILTERS.map(([value, label]) =>
      h("button", { type: "button", class: "tab", dataset: { filter: value }, onClick: () => setFilter(value) }, label)));
    $("library-search").addEventListener("input", () => render());
    bindAction("library-request", () => request({ items: chosen() }, { label: `${plural(chosen().length, "selected item")}` }));
    bindAction("library-retranslate", () => request({ items: chosen() }, { mode: "replace", label: `${plural(chosen().length, "selected item")}` }));
    bindAction("library-ignore", async () => decide("/api/catalog/ignore", chosen(), "Ignored: nothing will request these automatically"));
    bindAction("library-unignore", async () => decide("/api/catalog/unignore", chosen(), "No longer ignored"));
  },

  async enter(param) {
    target = param === "movies" ? "movies" : /^\d+$/.test(param || "") ? Number(param) : null;
    selected.clear();
    data = null;
    $("library-list").replaceChildren();
    setFilter(filter);
    await load(true);
  },

  tick() {
    // Jobs move on their own; do not redraw under a hand that is typing, choosing or ticking a box.
    const busy = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
    if (!document.hidden && !busy) load();
  },
};
