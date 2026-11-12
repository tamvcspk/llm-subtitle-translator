// Library: every series and movie the worker has translated, and which translations need attention.
// `#library` lists series and movies; `#library/<series id>` lists that series' episodes.
import { get } from "../api.js";
import { plural } from "../format.js";
import { crumbs, emptyState, errorState, h, icon, loadingState, setHeading, STATUS_LABELS, statusPill } from "../ui.js";

const FILTERS = [["", "All"], ["stale", "Stale"], ["incomplete", "Incomplete"], ["untranslated", "Untranslated"], ["current", "Current"]];
const ATTENTION = ["stale", "incomplete", "untranslated", "current"];
const $ = (id) => document.getElementById(id);

let filter = "";
let seriesId = null;
let loadId = 0;

function setFilter(value) {
  filter = value;
  $("library-tabs").querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.filter === filter) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  load();
}

function countChips(counts) {
  return ATTENTION.filter((status) => counts[status]).map((status) =>
    h("span", { class: `chip chip-${status}`, title: STATUS_LABELS[status] }, `${counts[status]} ${STATUS_LABELS[status].toLowerCase()}`));
}

function row(href, title, subtitle, middle, action) {
  return h("a", { class: "row row-link", href },
    h("div", { class: "row-main" }, h("div", { class: "row-title", text: title }), subtitle && h("div", { class: "row-path", text: subtitle })),
    h("div", { class: "row-langs" }, middle),
    h("div", { class: "row-action" }, action, icon("chevron")));
}

/** The language most in need of a look decides where the episode link lands. */
function leadLang(unit) {
  const ordered = [...unit.targets].sort((a, b) => ATTENTION.indexOf(a.status) - ATTENTION.indexOf(b.status));
  return ordered[0]?.lang;
}

function unitRow(unit) {
  const code = `S${String(unit.season ?? 0).padStart(2, "0")}E${String(unit.episode ?? 0).padStart(2, "0")}`;
  const lang = leadLang(unit);
  return row(`#episode/${unit.id}${lang ? `/${lang}` : ""}`,
    unit.series_id == null ? (unit.title || "Movie") : `${code}${unit.title ? ` · ${unit.title}` : ""}`,
    null,
    unit.targets.map((target) => h("span", { class: `chip chip-${target.status}`, title: `${target.lang}: ${STATUS_LABELS[target.status]}` },
      target.lang, target.modified_outside && icon("edit"))),
    statusPill(unit.status));
}

async function showOverview(token) {
  const [series, movies] = await Promise.all([get("/api/library/series"), get("/api/library/movies")]);
  if (token !== loadId) return;
  const shown = series.items.filter((item) => !filter || item.counts[filter]);
  const shownMovies = movies.items.filter((unit) => !filter || unit.status === filter);
  setHeading("Library", "Series and movies this worker has translated, and what needs attention.");
  crumbs($("library-crumbs"), [["Library"]]);
  const list = $("library-list");
  if (!shown.length && !shownMovies.length) {
    list.replaceChildren(filter
      ? emptyState("Nothing here", `No series or movies are ${STATUS_LABELS[filter].toLowerCase()}.`)
      : emptyState("Nothing translated yet", "Series and movies appear here after the worker translates their first subtitle.", "library"));
    return;
  }
  list.replaceChildren(
    ...(shown.length ? [h("div", { class: "group-title", text: "Series" }),
      ...shown.map((item) => row(`#library/${item.id}`, item.title || item.key, plural(item.units, "episode"), countChips(item.counts), null))] : []),
    ...(shownMovies.length ? [h("div", { class: "group-title", text: "Movies" }), ...shownMovies.map(unitRow)] : []));
}

async function showSeries(id, token) {
  const data = await get(`/api/library/series/${id}/units${filter ? `?status=${filter}` : ""}`);
  if (token !== loadId) return;
  setHeading(data.series.title || data.series.key, "Episodes and the state of their translations.");
  crumbs($("library-crumbs"), [["Library", "#library"], [data.series.title || data.series.key]]);
  const list = $("library-list");
  const glossary = data.languages.map((lang) => h("a", { class: "btn btn-sm", href: `#glossary/${id}/${lang}` }, icon("glossary"), `Glossary · ${lang}`));
  const head = h("div", { class: "series-tools" }, glossary.length ? glossary : h("a", { class: "btn btn-sm", href: `#glossary/${id}/vi` }, icon("glossary"), "Glossary"));
  if (!data.units.length) {
    list.replaceChildren(head, filter ? emptyState("No matching episodes", "Try another filter.") : emptyState("No episodes yet", "", "library"));
    return;
  }
  const seasons = new Map();
  for (const unit of data.units) {
    const season = unit.season ?? 0;
    if (!seasons.has(season)) seasons.set(season, []);
    seasons.get(season).push(unit);
  }
  list.replaceChildren(head, ...[...seasons].flatMap(([season, units]) => [
    h("div", { class: "group-title", text: season === 0 ? "Specials" : `Season ${season}` }),
    ...units.map(unitRow),
  ]));
}

async function load() {
  const token = ++loadId;
  const list = $("library-list");
  if (!list.children.length) list.replaceChildren(loadingState("Loading library…"));
  try {
    if (seriesId) await showSeries(seriesId, token);
    else await showOverview(token);
  } catch (error) {
    if (token === loadId) list.replaceChildren(errorState(error.message, load));
  }
}

export default {
  title: "Library",
  subtitle: "Series and movies this worker has translated.",

  init() {
    $("library-tabs").replaceChildren(...FILTERS.map(([value, label]) =>
      h("button", { type: "button", class: "tab", dataset: { filter: value }, onClick: () => setFilter(value) }, label)));
  },

  async enter(param) {
    seriesId = /^\d+$/.test(param || "") ? Number(param) : null;
    $("library-list").replaceChildren();
    setFilter(filter);
  },
};
