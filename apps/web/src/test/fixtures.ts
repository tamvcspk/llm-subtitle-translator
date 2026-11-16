import type { CatalogItem, CatalogSeries, CatalogTarget } from "../api/types";

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
    text: "Missing",
    ...overrides,
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
