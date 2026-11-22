import type { Correction, CueRow, UnitDetail, UnitTarget } from "../api/types";
import { series } from "./fixtures";

export const status = { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3, listed: 3, auto_request: false, auto_request_daily_cap: 0 };

export const base = {
  "/api/status": {
    body: {
      queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0, cancelled: 0 },
      daily_quota_pause: null,
      providers: [],
      settings: { key_warnings: [] },
    },
  },
  "/api/catalog/status": { body: status },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/catalog": {
    body: { status, languages: ["vi"], series: [series({ id: 4, title: "Arcane", series_id: 9 })], movies: { count: 0, missing: {}, failed: {}, ignored: {} } },
  },
};

export const target = (overrides: Partial<UnitTarget> = {}): UnitTarget => ({
  lang: "vi",
  language: "Vietnamese",
  exists: true,
  status: "current",
  version: 3,
  trigger: "cue_edit",
  model: "gemini-3.1-flash-lite",
  missing_units: [],
  updated_at: 1_800_000_000,
  modified_outside: false,
  removed: false,
  ...overrides,
});

export const episode = (overrides: Partial<UnitDetail> = {}): UnitDetail => ({
  id: 7,
  series_id: 9,
  season: 1,
  episode: 8,
  title: "Oil and Water",
  source_code: "en",
  targets: [target()],
  status: "current",
  series: { id: 9, title: "Arcane" },
  source_exists: true,
  memory_applicable: true,
  ...overrides,
});

/** An active correction of unit 7, in Vietnamese. */
export const correction = (id: number, kind: Correction["kind"], payload: Correction["payload"]): Correction => ({
  id,
  unit_id: 7,
  target_lang: "vi",
  kind,
  payload,
  created_at: 1,
  active: true,
});

export const cue = (n: number, overrides: Partial<CueRow> = {}): CueRow => ({
  n,
  index: n,
  start: "00:00:01",
  start_ms: n * 1000,
  end_ms: n * 1000 + 800,
  source: `Source line ${n}`,
  target: `Target line ${n}`,
  locked: false,
  ...overrides,
});

export const cues = (rows: CueRow[], overrides = {}) => ({
  lang: "vi",
  total: rows.length,
  offset: 0,
  focus: null,
  aligned: true,
  has_target: true,
  rows,
  ...overrides,
});
