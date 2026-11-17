// Shapes of the worker's answers (docs/API.md). Only what a screen reads is typed; add fields as screens need them.

export type ProviderState = "closed" | "open" | "exhausted" | "invalid" | "half_open";

export interface ProviderHealth {
  model: string;
  state: ProviderState;
  reason: string;
  /** Unix seconds when the provider may be tried again. */
  until: number | null;
  failures: number;
  usable: boolean;
}

export interface KeyWarning {
  kind: "key-invalid" | "provider-unconfigured" | "model-not-found" | (string & {});
  message: string;
  provider?: string;
  model?: string;
  key_id?: number | string;
  label?: string;
}

/** `GET /api/queue` counts and `GET /api/status` queue: jobs per console state. */
export interface QueueCounts {
  pending: number;
  processing: number;
  deferred: number;
  done: number;
  failed: number;
}

export interface StatusResponse {
  queue: QueueCounts;
  daily_quota_pause: { retry_at: number } | null;
  providers: ProviderHealth[];
  settings: { key_warnings: KeyWarning[] };
  /** How the default and the strong model are doing, read from the job rows (`GET /api/status`). */
  model_layers?: ModelLayers;
}

export interface ModelLayers {
  by_model: Record<string, { done: number; failed: number }>;
  escalated: { total: number; done: number; failed: number; waiting: number };
  /** How far back the counts reach; `0` means that kind of job is never deleted. */
  window: { done_days: number; failed_days: number };
}

/** One job of `GET /api/queue`. Only what a screen reads is typed. */
export interface QueueJob {
  job_id: string;
  /** The series, or the movie; empty for a job made before units existed. */
  title: string;
  /** `S01E04`; empty for a movie. */
  label: string;
  unit_id?: number;
  source_code?: string;
  target_code?: string;
  subtitle_path?: string;
  output_path?: string;
  /** `manual`, `policy`, `retranslate`, `variant`, … */
  origin?: string;
  /** `fill` or `replace`. */
  mode?: string;
  trigger?: string;
  stage?: string;
  progress_done?: number | null;
  progress_total?: number | null;
  /** `quota`, `provider`, `context`, `settle` or `rate`; waiting jobs only. */
  wait_reason?: string | null;
  /** Unix seconds, waiting jobs only. */
  retry_at?: number | null;
  attempts?: number;
  error?: string;
  missing_units?: string[];
  result?: string;
  /** Unix seconds. */
  created_at?: number;
}

/** `GET /api/queue`: the latest 100 jobs of each state, and the real counts. */
export interface QueueAnswer {
  counts: QueueCounts;
  total: number;
  daily_quota_pause: { retry_at: number } | null;
  providers: ProviderHealth[];
  pending: QueueJob[];
  processing: QueueJob[];
  deferred: QueueJob[];
  done: QueueJob[];
  failed: QueueJob[];
}

export interface CatalogStatus {
  /** Unix seconds of the last accepted read of Bazarr, or null before the first one. */
  synced_at: number | null;
  attempted_at: number | null;
  status: "ok" | "kept" | "";
  warning: string;
  items: number;
}

export interface AuthMe {
  user: string | null;
  via: "session" | "basic" | "off";
  can_change_password: boolean;
}

// -- the catalog (docs/API.md, "The catalog") ---------------------------------------------------------------------

export type JobAxis = "requested" | "running" | "waiting" | "failed" | "";

/** The job a row can act on (Cancel, Stop, Retry) and what it shows about it. */
export interface JobInfo {
  job_id: string;
  progress_done: number | null;
  progress_total: number | null;
  /** Unix seconds, waiting jobs only. */
  retry_at: number | null;
  error: string;
  /** `["S01E03"]`: the earlier episodes a `context` wait is for. */
  missing_units: string[];
}

/** One (item, language): the content axis, the job axis and the decision, with the label the console shows. */
export interface CatalogTarget {
  lang: string;
  content: "missing" | "translated" | "edited" | "external" | "outside";
  removed: boolean;
  stale: boolean;
  job: JobAxis;
  wait: string;
  ignored: boolean;
  /** Why `auto_request` has not asked for this Missing item yet; `bootstrap` = its series' glossary awaits review. */
  reason: "" | "bootstrap";
  text: string;
  job_info: JobInfo | null;
}

export interface CatalogItem {
  id: number;
  kind: "series" | "episode" | "movie";
  bazarr_id: number;
  title: string;
  season: number | null;
  episode: number | null;
  /** Set once the worker keeps a unit for the item; it opens the Episode or Unit page. */
  unit_id: number | null;
  source: string | null;
  has_source: boolean;
  gone: boolean;
  targets: CatalogTarget[];
}

/** Counts per target language, `{"vi": 3}`. */
export type PerLang = Record<string, number>;

export interface CatalogSeries {
  id: number;
  title: string;
  key: string;
  episodes: number;
  missing: PerLang;
  /** `requested`, `running`, `waiting`: open jobs of the series by state. */
  open_jobs: Partial<Record<"requested" | "running" | "waiting", number>>;
  failed: PerLang;
  ignored: PerLang;
  auto_request: "" | "on" | "off";
  series_id: number | null;
}

export interface CatalogOverview {
  status: CatalogStatus;
  /** Enabled target languages, lower case. */
  languages: string[];
  series: CatalogSeries[];
  movies: { count: number; missing: PerLang; failed: PerLang; ignored: PerLang };
}

export interface MoviesPage {
  status: CatalogStatus;
  languages: string[];
  items: CatalogItem[];
}

/** `GET /api/catalog/series/{id}`: one series with the label of every episode and language. */
export interface SeriesPage {
  status: CatalogStatus;
  languages: string[];
  series: {
    id: number;
    title: string;
    key: string;
    auto_request: "" | "on" | "off";
    series_id: number | null;
    gone: boolean;
    bootstrap: SeriesBootstrap;
  };
  items: CatalogItem[];
}

/**
 * While nothing of a series is approved, `auto_request` asks for its first `limit` episodes only (FR-15.12). `locked`
 * says it is holding the rest back (`held` labels); `pending` is how many suggestions wait for review.
 */
export interface SeriesBootstrap {
  locked: boolean;
  limit: number;
  pending: number;
  released: boolean;
  held: number;
}

/** `GET /api/library/series/{id}/units`: what the version history says about each episode's file. */
export interface LibraryTarget {
  lang: string;
  status: "current" | "stale" | "incomplete" | "untranslated";
  version: number | null;
  model: string;
  /** Unix seconds. */
  updated_at: number | null;
  modified_outside: boolean;
}

export interface LibraryUnit {
  id: number;
  season: number | null;
  episode: number | null;
  targets: LibraryTarget[];
}

export interface LibraryUnits {
  languages: string[];
  units: LibraryUnit[];
}

// -- one episode or movie (`/api/units/{id}`) ---------------------------------------------------------------------

export interface UnitTarget {
  lang: string;
  language: string;
  /** The target file is on disk. */
  exists: boolean;
  status: "current" | "stale" | "incomplete" | "untranslated";
  version: number | null;
  trigger: string | null;
  model: string;
  missing_units: string[];
  /** Unix seconds. */
  updated_at: number | null;
  modified_outside: boolean;
  removed: boolean;
}

export interface UnitDetail {
  id: number;
  /** The worker's own series id (not the catalog id), `null` for a movie. */
  series_id: number | null;
  season: number | null;
  episode: number | null;
  title: string;
  targets: UnitTarget[];
  series: { id: number; title: string } | null;
  source_exists: boolean;
  /** An episode of a series: the Context tab applies. */
  memory_applicable: boolean;
}

export interface CueRow {
  /** 1-based number in the source subtitle, the one the model sees. */
  n: number;
  start: string;
  start_ms: number;
  end_ms: number;
  source: string;
  /** `null` where the target has no such cue (or no target exists). */
  target: string | null;
  locked: boolean;
}

export interface CuesPage {
  lang: string;
  total: number;
  offset: number;
  focus: number | null;
  aligned: boolean;
  has_target: boolean;
  rows: CueRow[];
}

export interface Correction {
  id: number;
  kind: "term" | "address" | "note" | "cue_edit";
  payload: Record<string, unknown>;
  created_at: number;
}

// -- style (`/api/series/{id}/style`, `/api/movies/{id}/style`, `/api/units/{id}/style`) --------------------------

/** Every field is optional: an axis left out inherits from the layer below (language < preset < series < episode < card). */
export interface Style {
  formality?: "casual" | "neutral" | "formal";
  tone?: string;
  profanity?: "keep" | "soften" | "remove";
  localization?: "literal" | "balanced" | "adapt";
  dialect?: "north" | "south" | "neutral";
  honorifics?: "keep" | "translate";
  max_cps?: number;
  max_line_length?: number;
  max_lines?: number;
  allow_condense?: boolean;
  notes?: string;
}

export interface Preset {
  id: string;
  name: string;
  genres: string[];
  style: Style;
  builtin: boolean;
}

export interface StyleOverview {
  lang: string;
  preset: string;
  style: Style;
  /** What is in force once the language and the preset are laid under the owner's own style. */
  effective: Style;
  presets: Preset[];
  /** The voice of each character that has one (`PUT …/cards`). */
  cards: CharacterCard[];
}

/** The Voice of a character (UX §6.3): how they speak and where their style differs from the series'. */
export interface CharacterCard {
  name: string;
  aliases?: string[];
  speech?: string;
  /** Older cards may hold `(to, form)` pairs; the form never writes them (the glossary's Forms of address do). */
  address?: Array<{ to: string; form: string }>;
  catchphrases?: string[];
  samples?: string[];
  style?: Style;
  always?: boolean;
}

// -- glossary (`/api/series/{id}/glossary`, `/api/movies/{id}/glossary`) ------------------------------------------------

/** A hard rule: where `source` shows up, the output says `target`. `from`/`until` are `S01E06` (or `S01E06 00:32:10`). */
export interface GlossaryTerm {
  source: string;
  target: string;
  aliases?: string[];
  locked?: boolean;
  note?: string;
  from?: string;
  until?: string;
}

/** The Name of a character: the translated name, gender and a short description. */
export interface GlossaryCharacter {
  name: string;
  target?: string;
  gender?: string;
  description?: string;
}

/** Who calls whom how, as `self–other`. */
export interface GlossaryAddress {
  a: string;
  b: string;
  a_to_b?: string;
  b_to_a?: string;
  from?: string;
  until?: string;
  note?: string;
}

export interface GlossaryDoc {
  version?: number;
  target_lang?: string;
  style?: string;
  terms?: GlossaryTerm[];
  characters?: GlossaryCharacter[];
  address_rules?: GlossaryAddress[];
}

export interface GlossaryAnswer {
  lang: string;
  exists: boolean;
  doc: GlossaryDoc;
  yaml: string;
  /** How many times it was saved; 0 when there is none. */
  version: number;
  stale_units: number;
}

/** A save, or a dry run of one (`saved: false`): what it flags stale. Nothing is retranslated. */
export interface GlossarySave extends StyleImpact {
  version: number | null;
}

export interface GlossaryRevision {
  version: number;
  /** Unix seconds. */
  at: number;
  note: "console" | "import" | "correction" | "restore" | "copy" | (string & {});
  terms: number;
  characters: number;
  address_rules: number;
}

/** What saving would do to the translations made without it. Nothing is retranslated. */
export interface StyleImpact {
  ok: boolean;
  saved: boolean;
  stale_now: number;
  stale_after: number;
  would_stale: number;
}

export interface PreviewRow {
  n: number;
  source: string;
  /** What the current translation says; `null` when there is none. */
  current: string | null;
  preview: string;
}

export interface StylePreview {
  rows: PreviewRow[];
  model: string;
  tokens_in: number;
  tokens_out: number;
}

export interface ContextSection {
  title: string;
  body: string;
}

/** `GET /api/units/{id}/context`: stored facts only, never a model call. */
export interface UnitContextAnswer {
  applicable: boolean;
  enabled: boolean;
  label?: string;
  /** The context of the current version of the target; `null` when it was made without memory. */
  used?: { version: number; complete: boolean; missing: string[]; changed: boolean; sections: ContextSection[] } | null;
  /** What a translation started now would get. */
  now?: { approximate: boolean; sections: ContextSection[]; missing: string[] };
}

/** One episode of `GET /api/series/{id}/memory`, in story order. */
export interface MemoryUnit {
  id: number;
  label: string;
  season: number | null;
  episode: number | null;
  has_source: boolean;
  has_facts: boolean;
  facts_hash: string;
}

/** The series' story-memory switch: `""` follows the global `memory_enabled`. */
export type MemoryPolicy = "" | "on" | "off";

export interface SeriesMemory {
  series: { id: number; title: string };
  policy: MemoryPolicy;
  /** Where season 0 sits in the story (`S02E05`); blank is after the last episode of the first season. */
  specials_after: string;
  global_enabled: boolean;
  units: MemoryUnit[];
}

/** `POST /api/units/{id}/context/extract`: a result per episode: `cached`, `extracted`, `stored`, `no-source` or `failed`. */
export interface ExtractAnswer {
  ok: boolean;
  extracted: string[];
  results: Record<string, string>;
}

export interface Version {
  version: number;
  /** `auto`, `retranslate`, `variant`, `cue_edit`, `revert`, `external`, `remove`. */
  trigger: string;
  /** `machine`, `edit`, `revert`, `external`. */
  origin: string;
  status: "current" | "superseded" | "candidate" | "removed" | "pruned";
  parent_version: number | null;
  model: string;
  tokens_in: number | null;
  tokens_out: number | null;
  /** Unix seconds. */
  created_at: number;
  current: boolean;
  params: { range?: { first: number; last: number }; variant?: { label: string; preset: string; style?: Style } };
}

export interface VersionsAnswer {
  lang: string;
  modified_outside: boolean;
  versions: Version[];
}

export interface CompareRow {
  n: number;
  source: string;
  current: string | null;
  variant: string | null;
  differs: boolean;
}

/** `GET /api/units/{id}/versions/{v}/compare`: the current translation and one version, cue by cue. */
export interface CompareAnswer {
  lang: string;
  version: number;
  status: Version["status"];
  params: Version["params"] & { variant?: { label: string; preset: string; style: Style } };
  model: string;
  aligned: boolean;
  differing: number;
  rows: CompareRow[];
}

export interface DiffCue {
  n: number;
  text: string;
  time?: string;
}

export interface DiffAnswer {
  version: number;
  against: number | null;
  changed_cues: number;
  changes: Array<{ op: string; before: DiffCue[]; after: DiffCue[] }>;
}

// -- requesting translations (POST /api/jobs) ---------------------------------------------------------------------

/** Name what a request is about in exactly one way. */
export type JobsSelector = (
  | { items: number[] }
  | { series: number; season?: number }
  | { all: true }
) & {
  /** Only these target languages (default: every enabled one). */
  languages?: string[];
  /** `fill` (default) never overwrites; `replace` is a retranslation. */
  mode?: "fill" | "replace";
};

export interface Estimate {
  jobs: number;
  cues: number;
  calls: number | null;
  calls_left_today: number | null;
  /** Unix seconds; set only while the daily quota is spent. */
  paused_until: number | null;
  minutes: number | null;
  finish_at: number | null;
}

export interface JobsAnswer {
  estimate_only: boolean;
  estimate: Estimate;
  planned_total: number;
  skipped_total: number;
  skipped_by_reason: Record<string, number>;
  /** Present on a real request. */
  created?: string[];
  count?: number;
}

// -- Review inbox (`/api/suggestions`, P13) ---------------------------------------------------------------------------

export type SuggestionKind = "term" | "address_rule" | "character";
export type SuggestionSource = "facts" | "memory" | "feedback" | "research";
export type SuggestionStatus = "pending" | "accepted" | "rejected";

/** A character a generator proposes: its Name (glossary entry) and, optionally, its Voice (the card). */
export interface CharacterPayload extends GlossaryCharacter {
  card?: CharacterCard;
}

export type SuggestionPayload = GlossaryTerm | GlossaryAddress | CharacterPayload;

/** What shows why: a cue and its timecode (`text`, `timecode`, `unit_id`, `cue`), or a line a person can check. */
export type Evidence =
  | string
  | { text?: string; timecode?: string; episode?: string; unit_id?: number; cue?: number; url?: string };

export interface Suggestion {
  id: number;
  series_id: number;
  series: string;
  lang: string;
  kind: SuggestionKind;
  source: SuggestionSource;
  payload: SuggestionPayload;
  evidence: Evidence[];
  /** URLs of a research answer. */
  sources: string[];
  status: SuggestionStatus;
  auto_accepted: boolean;
  /** Unix seconds; 0 while pending. */
  decided_at: number;
  created_at: number;
  /** An accepted suggestion that changed the glossary can be undone. */
  undoable: boolean;
  /** Accepted after the admin changed it. */
  edited: boolean;
}

export interface SuggestionSummary {
  counts: Record<SuggestionStatus, number>;
  /** The pending total: what the badge on the nav shows. */
  pending: number;
  series: Array<{ series_id: number; series: string; pending: number }>;
}

export interface SuggestionList extends SuggestionSummary {
  items: Suggestion[];
}

/** An accept or an undo, or a dry run of one (`saved: false`): what it flags stale. Nothing is retranslated. */
export interface SuggestionDecision extends StyleImpact {
  unchanged?: boolean;
  suggestion?: Suggestion;
}

export interface BulkAnswer {
  ok: boolean;
  saved: boolean;
  count: number;
  stale_now?: number;
  stale_after?: number;
  would_stale: number;
  failed?: Array<{ id: number; error: string }>;
  series?: string;
}
