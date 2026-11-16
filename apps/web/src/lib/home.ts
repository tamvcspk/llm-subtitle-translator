import type { CatalogItem, CatalogOverview, CatalogSeries, JobsSelector, PerLang } from "../api/types";

export type Filter = "all" | "missing" | "running" | "failed" | "ignored";

/** What the one button of a row does (UX §5, "Một dòng Home"). */
export type RowAction =
  | { kind: "view-failed"; to: string }
  | { kind: "translate"; selector: JobsSelector; title: string }
  | { kind: "improve"; to: string }
  | { kind: "none" };

export interface Row {
  key: string;
  kind: "TV" | "MV";
  title: string;
  /** Where the title goes; null while the worker keeps no unit for a movie. */
  to: string | null;
  /** The facts under the title, in the order of the wireframe. */
  facts: string[];
  action: RowAction;
  /** For the chips. */
  missing: boolean;
  running: boolean;
  failed: boolean;
  ignored: boolean;
}

const total = (counts: PerLang): number => Object.values(counts).reduce((sum, n) => sum + n, 0);

/** The names the person sees for the target language ("Vietnamese"); the code when the browser does not know it. */
export function languageName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export function seriesRow(series: CatalogSeries): Row {
  const missing = total(series.missing);
  const failed = total(series.failed);
  const ignored = total(series.ignored);
  const requested = series.open_jobs.requested ?? 0;
  const running = series.open_jobs.running ?? 0;
  const waiting = series.open_jobs.waiting ?? 0;
  const open = requested + running + waiting;

  const facts = [`${series.episodes} eps`, `Missing ${missing}`];
  if (requested) facts.push(`Requested ${requested}`);
  if (running) facts.push(`Running ${running}`);
  if (waiting) facts.push(`Waiting ${waiting}`);
  if (failed) facts.push(`Failed ${failed}`);

  // Failed first, then missing items nobody has asked for yet, then Improve. A series whose missing items are all
  // covered by open jobs gets no Translate button: it would only make duplicates.
  let action: RowAction;
  if (failed > 0) action = { kind: "view-failed", to: `/series/${series.id}/episodes?status=failed` };
  else if (missing > open) action = { kind: "translate", selector: { series: series.id }, title: series.title };
  else action = { kind: "improve", to: `/series/${series.id}/style` };

  return {
    key: `series:${series.id}`,
    kind: "TV",
    title: series.title,
    to: `/series/${series.id}/episodes`,
    facts,
    action,
    missing: missing > 0,
    running: open > 0,
    failed: failed > 0,
    ignored: ignored > 0,
  };
}

export function movieRow(movie: CatalogItem): Row {
  const targets = movie.targets;
  const single = targets.length <= 1;
  const facts = targets.map((target) => (single ? target.text : `${target.lang.toUpperCase()}: ${target.text}`));
  const failed = targets.some((target) => target.job === "failed");
  const open = targets.some((target) => target.job !== "" && target.job !== "failed");
  const unaskedMissing = targets.some((target) => target.content === "missing" && target.job === "" && !target.ignored);
  const hasSubtitle = targets.some((target) => ["translated", "edited", "external"].includes(target.content));
  const unit = movie.unit_id;
  if (!movie.has_source && targets.some((target) => target.content === "missing")) facts.push("No source subtitle");

  let action: RowAction = { kind: "none" };
  if (failed && unit !== null) action = { kind: "view-failed", to: `/unit/${unit}` };
  else if (unaskedMissing && movie.has_source) action = { kind: "translate", selector: { items: [movie.id] }, title: movie.title };
  else if (hasSubtitle && unit !== null) action = { kind: "improve", to: `/unit/${unit}/style` };

  return {
    key: `movie:${movie.id}`,
    kind: "MV",
    title: movie.title,
    to: unit === null ? null : `/unit/${unit}`,
    facts: facts.length > 0 ? facts : ["No target language"],
    action,
    missing: targets.some((target) => target.content === "missing"),
    running: open,
    failed,
    ignored: targets.some((target) => target.ignored),
  };
}

export function buildRows(overview: CatalogOverview | undefined, movies: CatalogItem[] | undefined): Row[] {
  const rows = [
    ...(overview?.series ?? []).map(seriesRow),
    ...(movies ?? []).filter((movie) => !movie.gone).map(movieRow),
  ];
  return rows.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base", numeric: true }));
}

const fold = (text: string): string =>
  text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .replace(/đ/gi, "d")
    .toLowerCase();

/** "Running" counts any open job (requested, running or waiting): that is what a person looking for activity wants. */
export function filterRows(rows: Row[], query: string, filter: Filter): Row[] {
  const needle = fold(query.trim());
  return rows.filter((row) => {
    if (needle && !fold(row.title).includes(needle)) return false;
    switch (filter) {
      case "all":
        return true;
      case "missing":
        return row.missing;
      case "running":
        return row.running;
      case "failed":
        return row.failed;
      case "ignored":
        return row.ignored;
    }
  });
}
