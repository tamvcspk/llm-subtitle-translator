// Shapes of the worker's answers. They are generated: `schema.gen.ts` comes from `docs/openapi.json` (`npm run gen:api`),
// which comes from the pydantic models of `apps/server/subserver/api/answers`. A field changes there, not here. What this
// file keeps by hand is what the OpenAPI document cannot say: the names the screens use, and the shapes only the UI has
// (a request selector, a share).
import type { components, paths } from "./schema.gen";

type Schemas = components["schemas"];

type JsonOf<Response> = Response extends { content: { "application/json": infer Body } } ? Body : never;

/**
 * The answer of a route, taken from the document: `AnswerOf<"/api/units/{p1}/cues", "get">`. A path that is not in the
 * document, or has no such method, does not compile, so a call site cannot name a route the server does not have.
 */
export type AnswerOf<Path extends keyof paths, Method extends keyof paths[Path]> = paths[Path][Method] extends {
  responses: { 200: infer Ok };
}
  ? JsonOf<Ok>
  : never;

// -- status and queue ---------------------------------------------------------------------------------------------

export type ProviderHealth = Schemas["ProviderHealth"];
export type ProviderState = ProviderHealth["state"];
export type KeyWarning = Schemas["KeyWarning"];
/** `GET /api/queue` counts and `GET /api/status` queue: jobs per console state. */
export type QueueCounts = Schemas["QueueCounts"];
export type StatusResponse = Schemas["StatusAnswer"];
/** How the default and the strong model are doing, read from the job rows (`GET /api/status`). */
export type ModelLayers = Schemas["ModelLayers"];
/** One job of `GET /api/queue`. Only what a screen reads is typed. */
export type QueueJob = Schemas["QueueJob"];
/** `GET /api/queue`: the latest 100 jobs of each state, and the real counts. */
export type QueueAnswer = Schemas["QueueAnswer"];
export type AuthMe = Schemas["MeAnswer"];
/** `GET /api/settings`: every setting by its API name; a key is `**********` with `<field>_configured`, `_last4` and `_status`. */
export type SettingsAnswer = Schemas["SettingsAnswer"];

// -- the catalog (docs/API.md, "The catalog") ---------------------------------------------------------------------

export type CatalogStatus = Schemas["CatalogStatus"];
export type JobAxis = Schemas["CatalogTarget"]["job"];
/** The job a row can act on (Cancel, Stop, Retry) and what it shows about it. */
export type JobInfo = Schemas["JobInfo"];
/** One (item, language): the content axis, the job axis and the decision, with the label the console shows. */
export type CatalogTarget = Schemas["CatalogTarget"];
export type CatalogItem = Schemas["CatalogItem"];
/** Counts per target language, `{"vi": 3}`. */
export type PerLang = Record<string, number>;
export type CatalogSeries = Schemas["CatalogSeries"];
export type CatalogOverview = Schemas["CatalogOverview"];
export type MoviesPage = Schemas["MoviesPage"];
/** `GET /api/catalog/series/{id}`: one series with the label of every episode and language. */
export type SeriesPage = Schemas["SeriesPage"];
/** While nothing of a series is approved, `auto_request` asks for its first `limit` episodes only (FR-15.12). */
export type SeriesBootstrap = Schemas["SeriesBootstrap"];

// -- the library and one episode or movie (`/api/library/...`, `/api/units/{id}`) ------------------------------------

/** `GET /api/library/series/{id}/units`: what the version history says about each episode's file. */
export type LibraryTarget = Schemas["UnitTarget"];
export type LibraryUnit = Schemas["LibraryUnit"];
export type LibraryUnits = Schemas["LibraryUnits"];
export type UnitTarget = Schemas["UnitTarget"];
export type UnitDetail = Schemas["UnitDetail"];
export type CueRow = Schemas["CueRow"];
export type CuesPage = Schemas["CuesPage"];
export type Correction = Schemas["CorrectionRow"];
/** An entry of `GET /api/library/series`. */
export type LibrarySeriesRow = Schemas["LibrarySeriesRow"];

// -- style (`/api/series/{id}/style`, `/api/movies/{id}/style`, `/api/units/{id}/style`) --------------------------

/** Every field is optional: an axis left out inherits from the layer below (language < preset < series < episode < card). */
export type Style = Schemas["Style"];
export type Preset = Schemas["Preset"];
export type StyleOverview = Schemas["StyleOverview"];
/** The Voice of a character (UX §6.3): how they speak and where their style differs from the series'. */
export type CharacterCard = Schemas["CharacterCard"];
export type PreviewRow = Schemas["PreviewRow"];
export type StylePreview = Schemas["StylePreview"];

// -- glossary (`/api/series/{id}/glossary`, `/api/movies/{id}/glossary`) ------------------------------------------------

/** A hard rule: where `source` shows up, the output says `target`. `from`/`until` are `S01E06` (or `S01E06 00:32:10`). */
export type GlossaryTerm = Schemas["GlossaryTerm"];
/** The Name of a character: the translated name, gender and a short description. */
export type GlossaryCharacter = Schemas["GlossaryCharacter"];
/** Who calls whom how, as `self–other`. */
export type GlossaryAddress = Schemas["GlossaryAddress"];
export type GlossaryDoc = Schemas["GlossaryDoc"];
export type GlossaryAnswer = Schemas["GlossaryAnswer"];
/** A save, or a dry run of one (`saved: false`): what it flags stale. Nothing is retranslated. */
export type GlossarySave = Schemas["GlossarySave"];
/** A restore or a copy of a glossary, or a dry run of one (`saved: false`, `version: null`): what it flags stale. */
export type GlossaryChange = GlossarySave;
export type GlossaryRevision = Schemas["GlossaryRevision"];
/** What saving would do to the translations made without it. Nothing is retranslated. */
export type StyleImpact = Schemas["StyleImpact"];

// -- story memory ----------------------------------------------------------------------------------------------------

export type ContextSection = Schemas["ContextSection"];
/** `GET /api/units/{id}/context`: stored facts only, never a model call. */
export type UnitContextAnswer = Schemas["UnitContextAnswer"];
/** One episode of `GET /api/series/{id}/memory`, in story order. */
export type MemoryUnit = Schemas["MemoryUnit"];
/** The series' story-memory switch: `""` follows the global `memory_enabled`. */
export type MemoryPolicy = Schemas["SeriesMemory"]["policy"];
export type SeriesMemory = Schemas["SeriesMemory"];
/** `POST /api/units/{id}/context/extract`: one `extract` job per episode that needs reading; the others say why they were left out. */
export type ExtractAnswer = Schemas["ExtractAnswer"];
/** `GET /api/units/{id}/facts`: what the stored facts of an episode say. Read only, never a model call. */
export type UnitFacts = Schemas["UnitFacts"];

// -- versions and the comparison of a candidate ---------------------------------------------------------------------------

export type Version = Schemas["VersionRow"];
export type VersionsAnswer = Schemas["VersionsAnswer"];
/** What the LLM judge thought of one cue: advice, never the admin's pick. */
export type JudgeVerdict = Schemas["JudgeVerdict"];
export type CompareRow = Schemas["CompareRow"];
/** How the judge stands for one candidate (`judge` of `GET …/compare`; present when the worker has a judge). */
export type JudgeSummary = Schemas["JudgeSummary"];
/** `GET /api/units/{id}/versions/{v}/compare`: the current translation and one version, cue by cue. */
export type CompareAnswer = Schemas["CompareAnswer"];
export type DiffCue = Schemas["DiffCue"];
export type DiffAnswer = Schemas["DiffAnswer"];

// -- requesting translations (POST /api/jobs) ---------------------------------------------------------------------

/** Name what a request is about in exactly one way. (A request body: the UI's own shape, `SelectionBody` says it loosely.) */
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

export type Estimate = Schemas["Estimate"];
export type JobsAnswer = Schemas["JobsAnswer"];
/** What the Estimate sheet reads from the answer of any route that can say a price (`estimate_only`): the price, how many jobs, what was left out. */
export type EstimateAnswer = Pick<JobsAnswer, "estimate_only" | "estimate" | "planned_total" | "skipped_total" | "skipped_by_reason">;

// -- Review inbox (`/api/suggestions`, P13) ---------------------------------------------------------------------------

export type Suggestion = Schemas["Suggestion"];
export type SuggestionKind = Suggestion["kind"];
export type SuggestionSource = Suggestion["source"];
export type SuggestionStatus = Suggestion["status"];
/** A character a generator proposes: its Name (glossary entry) and, optionally, its Voice (the card). */
export type CharacterPayload = Schemas["CharacterPayload"];
export type SuggestionPayload = Suggestion["payload"];
/** What shows why: a cue and its timecode, or a line a person can check. */
export type Evidence = Suggestion["evidence"][number];
export type SuggestionSummary = Schemas["SuggestionSummary"];
export type SuggestionList = Schemas["SuggestionList"];
/** An accept or an undo, or a dry run of one (`saved: false`): what it flags stale. Nothing is retranslated. */
export type SuggestionDecision = Schemas["SuggestionDecision"];
export type BulkAnswer = Schemas["BulkAnswer"];
/** `POST /api/suggestions/from-edits`: what the admin's corrections of a series suggested. No model call. */
export type FromEditsAnswer = Schemas["FromEditsAnswer"];

// -- Insights (`/api/metrics`, `/api/series/{id}/golden`, P15) -------------------------------------------------------------

/** A share (0 to 1), or `null` when there was nothing to divide by: "no data" is not "0 %". */
export type Rate = number | null;
export type InsightsOperations = Schemas["InsightsOperations"];
export type InsightsQuality = Schemas["InsightsQuality"];
/** One model, or one prompt version: what it did and how much of it the admin corrected. */
export type InsightsBreakdown = Schemas["InsightsBreakdown"];
export type InsightsAb = Schemas["InsightsAb"];
export type InsightsDay = Schemas["InsightsDay"];
export type Insights = Schemas["Insights"];

// -- the rest of the console: the account, models, languages, connections, clean-up, backups, logs ------------------------

export type ModelChoice = Schemas["ModelChoice"];
export type ModelsAnswer = Schemas["ModelsAnswer"];
export type LanguageChoice = Schemas["LanguageChoice"];
export type LanguagesAnswer = Schemas["LanguagesAnswer"];
export type ConnectionResult = Schemas["ConnectionResult"];
export type CleanupGroup = Schemas["CleanupGroup"];
export type CleanupAnswer = Schemas["CleanupAnswer"];
export type BackupFile = Schemas["BackupFile"];
export type BackupsAnswer = Schemas["BackupsAnswer"];
export type BackupCreated = Schemas["BackupCreated"];
export type ImportAnswer = Schemas["ImportAnswer"];
export type LogEntry = Schemas["LogEntry"];
export type LogsAnswer = Schemas["LogsAnswer"];

// -- more answers of the library and the catalog -------------------------------------------------------------------------

export type LibrarySeriesAnswer = Schemas["LibrarySeriesAnswer"];
export type CorrectionsAnswer = Schemas["CorrectionsAnswer"];
/** `POST /api/units/{id}/corrections`: a cue edit adds `version`; a wider scope adds `impact`. Nothing is retranslated. */
export type CorrectionAnswer = Schemas["CorrectionAdded"];
export type GlossaryHistory = Schemas["GlossaryHistory"];
export type AdoptEpisodesAnswer = Schemas["AdoptEpisodesAnswer"];
export type ChooseAnswer = Schemas["ChooseAnswer"];
export type UnitStyleAnswer = Schemas["UnitStyleAnswer"];
export type StyleSuggestions = Schemas["StyleSuggestions"];
export type BootstrapAnswer = Schemas["BootstrapAnswer"];
export type IgnoreAnswer = Schemas["IgnoreAnswer"];

export type AdoptAnswer = Schemas["AdoptAnswer"];
/** `POST /api/queue/request-prerequisites`: the jobs made, and the episodes with no source subtitle yet. */
export type PrerequisitesRequested = Schemas["PrerequisitesRequested"];
