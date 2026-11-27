import type {
  CatalogItem,
  CatalogSeries,
  CatalogStatus,
  CatalogTarget,
  KeyWarning,
  ProviderHealth,
  QueueCounts,
  SeriesPage,
  SettingsAnswer,
} from "../api/types";

/** Jobs per console state: every state is always in the answer. */
export function queueCounts(overrides: Partial<QueueCounts> = {}): QueueCounts {
  return { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0, cancelled: 0, ...overrides };
}

export function provider(overrides: Partial<ProviderHealth> = {}): ProviderHealth {
  return { provider: "gemini", model: "m", state: "closed", reason: "", until: null, failures: 0, usable: true, ...overrides };
}

/** The settings of `GET /api/settings` the answer always names: keys never carry a value, only what to say about them. */
export function settingsAnswer(keyWarnings: KeyWarning[] = [], overrides: Partial<SettingsAnswer> = {}): SettingsAnswer {
  const key = (name: string) => ({
    [name]: "",
    [`${name}_configured`]: false,
    [`${name}_last4`]: "",
    [`${name}_status`]: "",
  });
  return {
    bazarr_url: "http://bazarr:6767",
    ...key("bazarr_api_key"),
    ...key("tmdb_api_key"),
    ...key("gemini_api_key"),
    ...key("gemini_api_key2"),
    ...key("openai_compat_api_key"),
    llm_provider: "gemini",
    llm_base_url: "",
    source_languages: [],
    target_languages: [],
    media_roots: [],
    key_warnings: keyWarnings,
    ...overrides,
  } as SettingsAnswer;
}

export function catalogStatus(overrides: Partial<CatalogStatus> = {}): CatalogStatus {
  return {
    synced_at: null,
    attempted_at: null,
    status: "ok",
    warning: "",
    items: 0,
    listed: 0,
    auto_request: false,
    auto_request_daily_cap: 0,
    ...overrides,
  };
}

export function series(overrides: Partial<CatalogSeries> = {}): CatalogSeries {
  return {
    id: 1,
    title: "Arcane",
    key: "arcane",
    episodes: 18,
    gone: false,
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
    bazarr_id: String(n),
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

/** The Bootstrap block of a series that is not held; a test overrides what it is about. */
export function bootstrapState(overrides: Partial<SeriesPage["series"]["bootstrap"]> = {}): SeriesPage["series"]["bootstrap"] {
  return {
    locked: false, limit: 2, pending: 0, released: false, held: 0, held_by_lang: { vi: 0 }, no_source: 0, glossary_empty: true,
    open_jobs: 0, memory_on: true, queued_policy_jobs: 0, relockable: false, ...overrides,
  };
}

export function seriesPage(items: CatalogItem[], overrides: Partial<SeriesPage["series"]> = {}): SeriesPage {
  return {
    status: catalogStatus({ items: items.length, listed: items.length }),
    languages: ["vi"],
    series: {
      id: 1,
      title: "Arcane",
      key: "arcane",
      auto_request: "",
      series_id: 9,
      gone: false,
      bootstrap: bootstrapState(),
      ...overrides,
    },
    items,
  };
}

export function movie(overrides: Partial<CatalogItem> = {}): CatalogItem {
  return {
    id: 100,
    kind: "movie",
    bazarr_id: "7",
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
