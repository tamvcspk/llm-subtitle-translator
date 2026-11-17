import type { CatalogItem, CatalogSeries, CatalogTarget, SeriesPage } from "../api/types";

export function series(overrides: Partial<CatalogSeries> = {}): CatalogSeries {
  return {
    id: 1,
    title: "Arcane",
    key: "arcane",
    episodes: 18,
    missing: { vi: 0 },
    open_jobs: {},
    failed: { vi: 0 },
    ignored: { vi: 0 },
    auto_request: "",
    series_id: 1,
    ...overrides,
  };
}

export function target(overrides: Partial<CatalogTarget> = {}): CatalogTarget {
  return {
    lang: "vi",
    content: "missing",
    removed: false,
    stale: false,
    job: "",
    wait: "",
    ignored: false,
    reason: "",
    text: "Missing",
    job_info: null,
    ...overrides,
  };
}

/** An episode of series 1; `n` is its number in season 1 and gives it an id of its own. */
export function episode(n: number, overrides: Partial<CatalogItem> = {}): CatalogItem {
  return {
    id: 1000 + n,
    kind: "episode",
    bazarr_id: n,
    title: `Episode ${n}`,
    season: 1,
    episode: n,
    unit_id: 500 + n,
    source: "en",
    has_source: true,
    gone: false,
    targets: [target()],
    ...overrides,
  };
}

export function seriesPage(items: CatalogItem[], overrides: Partial<SeriesPage["series"]> = {}): SeriesPage {
  return {
    status: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: items.length },
    languages: ["vi"],
    series: {
      id: 1,
      title: "Arcane",
      key: "arcane",
      auto_request: "",
      series_id: 9,
      gone: false,
      bootstrap: { locked: false, limit: 2, pending: 0, released: false, held: 0 },
      ...overrides,
    },
    items,
  };
}

export function movie(overrides: Partial<CatalogItem> = {}): CatalogItem {
  return {
    id: 100,
    kind: "movie",
    bazarr_id: 7,
    title: "Oppenheimer",
    season: null,
    episode: null,
    unit_id: 55,
    source: "en",
    has_source: true,
    gone: false,
    targets: [target()],
    ...overrides,
  };
}
