import type { CatalogItem, CatalogTarget, JobsSelector, LibraryTarget, LibraryUnit, SeriesPage } from "../api/types";
import type { EstimateRequest } from "../components/EstimateSheet";
import { ago, clock } from "./format";

export type StatusFilter = "all" | "missing" | "running" | "failed" | "stale";

/** The one button of a row (UX §5): what the person does next, by the label of that row. */
export type EpisodeAction =
  | { kind: "estimate"; label: string; request: EstimateRequest }
  /** Cancel a job that has not started, or ask a running one to stop. Costs nothing, so no estimate. */
  | { kind: "stop"; label: "Cancel" | "Stop"; jobId: string }
  | { kind: "unignore"; label: string; itemId: number }
  | { kind: "link"; label: string; to: string };

export interface EpisodeRow {
  id: number;
  unitId: number | null;
  /** `S01E04`. */
  code: string;
  title: string;
  /** The episode's own page; none until the worker keeps a unit for it. */
  to: string | null;
  /** `Missing · Waiting · quota`, the content, the job and the decision side by side. */
  label: string;
  /** Under the label: why it waits, why it failed, which version it is. */
  details: string[];
  warning: string | null;
  actions: EpisodeAction[];
  gone: boolean;
  /** Something the Translate buttons of the page would take. */
  askable: boolean;
  missing: boolean;
  running: boolean;
  failed: boolean;
  stale: boolean;
}

export interface Season {
  /** `null` for episodes the catalog gives no season. */
  season: number | null;
  title: string;
  rows: EpisodeRow[];
  askable: number;
}

const CONTENT = {
  missing: "Missing",
  translated: "Translated",
  edited: "Edited",
  external: "External",
  outside: "Outside profile",
} as const;

const pad = (n: number): string => String(n).padStart(2, "0");

export function episodeCode(season: number | null, episode: number | null): string {
  if (season === null || episode === null) return episode === null ? "—" : `E${pad(episode)}`;
  return `S${pad(season)}E${pad(episode)}`;
}

function jobText(target: CatalogTarget): string | null {
  const info = target.job_info;
  switch (target.job) {
    case "requested":
      return "Requested";
    case "running":
      return info?.progress_total ? `Running ${info.progress_done ?? 0}/${info.progress_total}` : "Running";
    case "waiting":
      return `Waiting · ${target.wait}`;
    case "failed":
      return "Failed";
    default:
      return null;
  }
}

export function rowLabel(target: CatalogTarget): string {
  return [
    CONTENT[target.content],
    target.removed ? "Removed" : null,
    target.stale ? "Stale" : null,
    jobText(target),
    target.ignored ? "Ignored" : null,
    target.reason === "bootstrap" ? "Waiting for glossary review" : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

function waitNote(target: CatalogTarget): string | null {
  const info = target.job_info;
  switch (target.wait) {
    case "quota":
      return info?.retry_at ? `resumes ${clock(info.retry_at)}` : "waits for the daily quota";
    case "provider":
      return info?.retry_at ? `retrying after ${clock(info.retry_at)}` : "the key or model was refused";
    case "context":
      return info && info.missing_units.length > 0 ? `needs ${info.missing_units.join(", ")}` : "needs earlier episodes";
    case "settle":
      return "source changed a moment ago";
    case "rate":
      return "provider busy, retrying";
    default:
      return null;
  }
}

function versionNote(file: LibraryTarget | undefined, nowMs: number): string | null {
  if (!file || file.version === null) return null;
  return [`v${file.version}`, file.model, file.updated_at ? ago(file.updated_at, nowMs) : ""].filter(Boolean).join(" · ");
}

function ask(action: string, selector: JobsSelector): EstimateRequest {
  return { action, selector };
}

/** The earlier episodes a `context` wait is for, as catalog ids: what "Request that episode" asks for. */
function prerequisiteIds(target: CatalogTarget, byCode: Map<string, number>): number[] {
  return (target.job_info?.missing_units ?? [])
    .map((code) => byCode.get(code.toUpperCase()))
    .filter((id): id is number => id !== undefined);
}

function actionsOf(
  item: CatalogItem,
  target: CatalogTarget,
  code: string,
  seriesId: number,
  lang: string,
  byCode: Map<string, number>,
): EpisodeAction[] {
  if (item.gone) return [];
  if (target.ignored) return [{ kind: "unignore", label: "Stop ignoring", itemId: item.id }];
  const info = target.job_info;
  const history = item.unit_id === null ? null : `/unit/${item.unit_id}/history`;
  const only = { items: [item.id], languages: [lang] };

  switch (target.job) {
    case "requested":
      return info ? [{ kind: "stop", label: "Cancel", jobId: info.job_id }] : [];
    case "running":
      return info ? [{ kind: "stop", label: "Stop", jobId: info.job_id }] : [];
    case "waiting": {
      if (target.wait === "context") {
        const ids = prerequisiteIds(target, byCode);
        return ids.length > 0
          ? [{ kind: "estimate", label: "Request that episode", request: ask(`Request ${info?.missing_units.join(", ")}`, { items: ids, languages: [lang] }) }]
          : [];
      }
      return target.wait === "provider" ? [{ kind: "link", label: "Open Connections", to: "/settings" }] : [];
    }
    case "failed":
      return info
        ? [{ kind: "estimate", label: "Retry", request: { action: `Retry · ${code}`, retry: { job_id: info.job_id } } }]
        : [];
    default:
      break;
  }

  if (target.removed) {
    const again: EpisodeAction = { kind: "estimate", label: "Translate again", request: ask(`Translate again · ${code}`, only) };
    // Restore is a revert of the removed version, done from History (the episode page, a later item of P12b).
    return history ? [{ kind: "link", label: "History", to: history }, again] : [again];
  }
  switch (target.content) {
    case "missing":
    case "outside":
      return item.has_source ? [{ kind: "estimate", label: "Translate", request: ask(`Translate · ${code}`, only) }] : [];
    case "translated":
      return target.stale
        ? [{ kind: "estimate", label: "Retranslate", request: ask(`Retranslate · ${code}`, { ...only, mode: "replace" }) }]
        : [{ kind: "link", label: "Improve", to: `/series/${seriesId}/style` }];
    case "edited":
      return history ? [{ kind: "link", label: "History", to: history }] : [];
    case "external":
      return item.unit_id === null ? [] : [{ kind: "link", label: "View", to: `/unit/${item.unit_id}` }];
  }
}

function buildRow(
  item: CatalogItem,
  target: CatalogTarget,
  file: LibraryTarget | undefined,
  seriesId: number,
  lang: string,
  byCode: Map<string, number>,
  nowMs: number,
): EpisodeRow {
  const code = episodeCode(item.season, item.episode);
  const details: string[] = [];
  const wait = target.job === "waiting" ? waitNote(target) : null;
  if (wait) details.push(wait);
  if (target.job === "failed" && target.job_info?.error) details.push(target.job_info.error);
  if (!item.has_source && target.content === "missing" && target.job === "") details.push("no source subtitle");
  if (item.gone) details.push("no longer in Bazarr");
  const version = target.content === "translated" || target.content === "edited" ? versionNote(file, nowMs) : null;
  if (version) details.push(version);

  const open = target.job === "requested" || target.job === "running" || target.job === "waiting";
  return {
    id: item.id,
    unitId: item.unit_id,
    code,
    title: item.title,
    to: item.unit_id === null ? null : `/unit/${item.unit_id}`,
    label: rowLabel(target),
    details,
    warning: file?.modified_outside ? "File changed outside the worker" : null,
    actions: actionsOf(item, target, code, seriesId, lang, byCode),
    gone: item.gone,
    askable: target.content === "missing" && !open && !target.ignored && item.has_source && !item.gone,
    missing: target.content === "missing",
    running: open,
    failed: target.job === "failed",
    stale: target.stale,
  };
}

/** Episodes of one series for one target language, grouped by season, in story order. */
export function buildSeasons(
  page: SeriesPage | undefined,
  units: LibraryUnit[] | undefined,
  lang: string,
  nowMs: number = Date.now(),
): Season[] {
  if (!page) return [];
  const files = new Map<number, LibraryTarget | undefined>(
    (units ?? []).map((unit) => [unit.id, unit.targets.find((candidate) => candidate.lang === lang)]),
  );
  const byCode = new Map<string, number>();
  for (const item of page.items) byCode.set(episodeCode(item.season, item.episode), item.id);

  const groups = new Map<number | null, EpisodeRow[]>();
  const order = new Map<number, [number, number]>();
  for (const item of page.items) {
    const target = item.targets.find((candidate) => candidate.lang === lang);
    if (!target) continue;
    const file = item.unit_id === null ? undefined : files.get(item.unit_id);
    const row = buildRow(item, target, file, page.series.id, lang, byCode, nowMs);
    order.set(item.id, [item.season ?? -1, item.episode ?? -1]);
    const rows = groups.get(item.season) ?? [];
    rows.push(row);
    groups.set(item.season, rows);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a ?? Infinity) - (b ?? Infinity))
    .map(([season, rows]) => ({
      season,
      title: season === null ? "Other" : season === 0 ? "Specials" : `Season ${season}`,
      rows: rows.sort((a, b) => (order.get(a.id)?.[1] ?? 0) - (order.get(b.id)?.[1] ?? 0)),
      askable: rows.filter((row) => row.askable).length,
    }));
}

/** Rows that stay under a status chip; seasons left with none are dropped, and `all` keeps everything. */
export function filterSeasons(seasons: Season[], filter: StatusFilter): Season[] {
  if (filter === "all") return seasons;
  return seasons
    .map((season) => ({ ...season, rows: season.rows.filter((row) => row[filter]) }))
    .filter((season) => season.rows.length > 0);
}

export function parseFilter(value: string | null): StatusFilter {
  return value === "missing" || value === "running" || value === "failed" || value === "stale" ? value : "all";
}
