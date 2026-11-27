/** The settings the console edits, by group (UX Appendix B). The names are the API's; the limits are the ones the worker clamps to. */

export type FieldKind = "int" | "float" | "numtext" | "switch" | "choice" | "model" | "zone";

export interface Field {
  key: string;
  label: string;
  kind: FieldKind;
  hint?: string;
  min?: number;
  max?: number;
  step?: number;
  options?: Array<{ value: string; label: string }>;
  /** `model`: the box can be emptied; `empty` says what an empty one means. */
  empty?: string;
}

export type Values = Record<string, unknown>;

/** Every box that chooses a model, kept together in group 2 so one Save sets how the quota is shared (FR-9.3.1). */
export const MODEL_FIELDS: Field[] = [
  { key: "gst_model", label: "Default model", kind: "model", hint: "Does the work." },
  { key: "gst_fallback_model", label: "Strong model", kind: "model", empty: "(none)", hint: "Takes hard jobs, outages and spent quota. Clear it to run on one model." },
  {
    key: "memory_model",
    label: "Memory model",
    kind: "model",
    empty: "(same as Default)",
    hint: "Runs Extract facts, Suggest from facts, and step 2 of Web research.",
  },
  {
    key: "summarize_model",
    label: "Summarize model",
    kind: "model",
    empty: "(same as Memory)",
    hint: "Condenses old story summaries (falls back to Memory, then Default).",
  },
  {
    key: "research_model",
    label: "Research model",
    kind: "model",
    empty: "(same as Default)",
    hint: "Uses Google Search grounding (Gemini only; skipped on OpenAI-compatible).",
  },
  {
    key: "judge_model",
    label: "Judge model",
    kind: "model",
    empty: "(same as Default)",
    hint: "A stronger model than the one that translated judges better.",
  },
];

/** The box after which group 2 shows the table of models by task and the warnings (they belong with the model boxes). */
export const LAST_MODEL_FIELD = "judge_model";

export const TRANSLATION_FIELDS: Field[] = [
  ...MODEL_FIELDS,
  {
    key: "research_enabled",
    label: "Research terms by itself",
    kind: "switch",
    hint: "Off by default. After a job reads new facts, look up on the web the names two episodes share. Results wait in Review (two independent sites apply a term by themselves when Auto-accept terms is on). The Research terms button always works. Google's Search terms apply: for your own use only; Google keeps the prompts 30 days.",
  },
  {
    key: "research_max_calls_per_series",
    label: "Research budget, calls per series",
    kind: "int",
    min: 0,
    hint: "Calls of the research task one series may use over its whole life. A run makes two. 0 = no cap.",
  },
  {
    key: "judge_enabled",
    label: "LLM judge",
    kind: "switch",
    hint: "Off by default. On a candidate in Compare, a model says which cues look better. Advice only, and every use spends model calls.",
  },
  { key: "gst_temperature", label: "Temperature", kind: "numtext", min: 0, max: 2, step: 0.1 },
  { key: "gst_thinking_budget", label: "Thinking budget", kind: "numtext", min: 0, step: 128 },
  { key: "gst_no_thinking", label: "No thinking", kind: "switch" },
  { key: "gst_paid_quota", label: "Paid quota tier", kind: "switch" },
  { key: "llm_max_input_tokens", label: "Max input tokens", kind: "int", min: 1000 },
  { key: "llm_max_output_tokens", label: "Max output tokens", kind: "int", min: 256 },
];

export const LIBRARY_FIELDS: Field[] = [
  { key: "catalog_sync_interval", label: "Sync every, seconds", kind: "int", min: 0, hint: "0 = only on the Sync now button." },
  { key: "catalog_shrink_guard", label: "Ignore a read that shrinks by, %", kind: "int", min: 0, max: 100, hint: "0 = no guard." },
  {
    key: "auto_request",
    label: "Auto-request",
    kind: "switch",
    hint: "Off by default. Translates missing items of chosen series without asking.",
  },
  { key: "auto_request_daily_cap", label: "Auto-request daily cap", kind: "int", min: 0, hint: "0 = no cap." },
  {
    key: "auto_request_bootstrap",
    label: "Auto-request first episodes",
    kind: "int",
    min: 0,
    hint: "A series with nothing approved gets this many episodes, then waits for its glossary to be reviewed. 0 = no wait.",
  },
];

export const QUEUE_FIELDS: Field[] = [
  { key: "job_settle_seconds", label: "Settle window, seconds", kind: "int", min: 0 },
  { key: "breaker_threshold", label: "Breaker threshold", kind: "int", min: 1 },
  { key: "max_crashes", label: "Max crashes", kind: "int", min: 1 },
  { key: "max_calls_per_job", label: "Max calls per job", kind: "int", min: 0, hint: "0 = no cap." },
  { key: "max_calls_per_day", label: "Max calls per day", kind: "int", min: 0, hint: "0 = no cap." },
  { key: "transient_retry_seconds", label: "Transient retry, seconds", kind: "int", min: 0 },
  {
    key: "quota_reset_tz",
    label: "Provider quota resets at midnight in",
    kind: "zone",
    hint: "This is the provider's midnight (Gemini), not your local time zone.",
  },
];

export const STORY_FIELDS: Field[] = [
  { key: "memory_enabled", label: "Story memory", kind: "switch" },
  { key: "memory_gap_wait_seconds", label: "Gap wait, seconds", kind: "int", min: 0, hint: "0 = never wait." },
  { key: "memory_budget_tokens", label: "Memory budget, tokens", kind: "int", min: 500 },
  { key: "memory_recent_episodes", label: "Recent episodes", kind: "int", min: 0 },
  { key: "few_shot_budget_tokens", label: "Few-shot budget, tokens", kind: "int", min: 0, hint: "0 = off." },
  { key: "glossary_filter_threshold", label: "Filter terms above, terms", kind: "int", min: 0, hint: "0 = never filter." },
  { key: "edit_coalesce_minutes", label: "Merge cue edits within, minutes", kind: "int", min: 0 },
  {
    key: "suggest_from_facts",
    label: "Suggest from new facts",
    kind: "switch",
    hint: "After a job reads a new episode, ask the Memory model for terms, forms of address and characters. They wait in Review.",
  },
  {
    key: "suggest_from_edits",
    label: "Suggest from my edits",
    kind: "switch",
    hint: "After you edit a cue, look for a word you keep changing in three or more cues. It waits in Review. No model call.",
  },
  {
    key: "auto_accept_terms",
    label: "Auto-accept terms",
    kind: "switch",
    hint: "Only a term that two independent sources agree on; every other one waits in Review.",
  },
  { key: "auto_accept_address_rules", label: "Auto-accept forms of address", kind: "switch", hint: "Off by default: who calls whom how is your call." },
  { key: "auto_accept_characters", label: "Auto-accept characters", kind: "switch", hint: "Off by default." },
];

export const MAINTENANCE_FIELDS: Field[] = [
  {
    key: "log_level",
    label: "Log level",
    kind: "choice",
    options: ["DEBUG", "INFO", "WARNING", "ERROR"].map((value) => ({ value, label: value })),
  },
  {
    key: "log_prompt_bodies",
    label: "Keep prompt bodies",
    kind: "choice",
    options: [
      { value: "all", label: "All" },
      { value: "sample", label: "Sample" },
      { value: "errors", label: "Errors only" },
      { value: "off", label: "Off" },
    ],
  },
  { key: "log_body_max_bytes", label: "Max body size, bytes", kind: "int", min: 1024 },
  { key: "log_max_mb", label: "Max log size, MB", kind: "int", min: 0, hint: "0 = no limit." },
  { key: "log_retention_days", label: "Keep logs, days", kind: "int", min: 0, hint: "0 = no limit." },
  { key: "events_retention_days", label: "Keep events, days", kind: "int", min: 0, hint: "0 = forever." },
  { key: "llm_cost_in_per_mtok", label: "Cost per million tokens in", kind: "float", min: 0, hint: "Only to estimate cost." },
  { key: "llm_cost_out_per_mtok", label: "Cost per million tokens out", kind: "float", min: 0 },
  { key: "retention_done_days", label: "Keep finished jobs, days", kind: "int", min: 0, hint: "0 = keep." },
  { key: "retention_failed_days", label: "Keep failed jobs, days", kind: "int", min: 0, hint: "0 = keep." },
  { key: "retention_versions_keep", label: "Keep versions per subtitle", kind: "int", min: 0, hint: "0 = keep all." },
  { key: "retention_glossary_keep", label: "Keep glossary history", kind: "int", min: 0, hint: "0 = keep all." },
];

/** The keys of a group, in one place so a Save sends exactly them and nothing of another group. */
export const keysOf = (fields: Field[]): string[] => fields.map((field) => field.key);

export function pick(settings: Values, keys: readonly string[]): Values {
  const out: Values = {};
  for (const key of keys) out[key] = settings[key];
  return out;
}

/** A number box that is empty or not a number: what the field's problem is, or `null` when it is fine. */
export function fieldProblem(field: Field, value: unknown): string | null {
  if (field.kind !== "int" && field.kind !== "float" && field.kind !== "numtext") return null;
  const text = typeof value === "string" ? value.trim() : value;
  if (text === "" || text === null || text === undefined) return "Enter a number";
  const n = Number(text);
  if (!Number.isFinite(n)) return "Enter a number";
  if (field.kind === "int" && !Number.isInteger(n)) return "Use a whole number";
  if (field.min !== undefined && n < field.min) return `At least ${field.min}`;
  if (field.max !== undefined && n > field.max) return `At most ${field.max}`;
  return null;
}

/** What goes in the body for a field: numbers stay numbers, except the two the worker stores as text. */
export function toWire(field: Field, value: unknown): unknown {
  if (field.kind === "numtext") return String(value).trim();
  if (field.kind === "int") return Math.round(Number(value));
  if (field.kind === "float") return Number(value);
  if (field.kind === "model") return typeof value === "string" ? value.trim() : "";
  return value;
}

/** Equal by what would be stored: `"0.70"` and `0.7` are the same temperature. */
export function sameValue(field: Field | undefined, a: unknown, b: unknown): boolean {
  if (field && (field.kind === "numtext" || field.kind === "int" || field.kind === "float")) {
    const x = Number(a);
    const y = Number(b);
    return Number.isNaN(x) || Number.isNaN(y) ? a === b : x === y;
  }
  if (field?.kind === "model") return String(a ?? "").trim() === String(b ?? "").trim();
  return JSON.stringify(a) === JSON.stringify(b);
}

const named = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

/** What an empty model box would run on, said in the box itself: "Same as Default (gemini-3.8-flash)" (FR-9.3.1). */
export function modelPlaceholder(field: Field, values: Values): string | undefined {
  const main = named(values.gst_model);
  const memory = named(values.memory_model);
  if (field.key === "summarize_model" && memory) return `Same as Memory (${memory})`;
  if (["memory_model", "summarize_model", "research_model", "judge_model"].includes(field.key) && main) return `Same as Default (${main})`;
  return field.empty;
}

export interface TaskRow {
  label: string;
  /** What it runs on now; empty when the task is off or has no model (the strong layer). */
  model: string;
  /** Where an empty box takes its model from: "(uses Default model)". */
  note: string;
  off?: string;
}

/**
 * The models by task as the boxes say now (the same rule as the worker's `model_for_task`, so it follows the unsaved
 * draft). `translate` is the default model; the others take their own box, else the one above them.
 */
export function activeModels(values: Values): TaskRow[] {
  const main = named(values.gst_model);
  const strong = named(values.gst_fallback_model);
  const memory = named(values.memory_model);
  const summarize = named(values.summarize_model);
  const research = named(values.research_model);
  const judge = named(values.judge_model);
  return [
    { label: "Translate", model: main, note: "" },
    {
      label: "Strong fallback",
      model: strong && strong !== main ? strong : "",
      note: "",
      off: strong && strong !== main ? undefined : "Off (single model)",
    },
    { label: "Extract facts", model: memory || main, note: memory ? "" : "(uses Default model)" },
    {
      label: "Condense story",
      model: summarize || memory || main,
      note: summarize ? "" : memory ? "(uses Memory model)" : "(uses Default model)",
    },
    { label: "Suggest from facts", model: memory || main, note: "(uses Extract facts model)" },
    { label: "Web research", model: research || main, note: research ? "" : "(uses Default model)" },
    {
      label: "LLM judge",
      model: judge || main,
      note: judge ? "" : "(uses Default model)",
      off: values.judge_enabled === true ? undefined : `Off  (${judge || main} when enabled)`,
    },
  ];
}

function joined(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * What a change of the model boxes would surprise the admin with, before it is saved: the tasks with no model of their
 * own that follow a changed default model, and a strong model equal to the default (the worker drops it on save).
 */
export function modelWarnings(stored: Values, draft: Values): string[] {
  const out: string[] = [];
  const main = named(draft.gst_model);
  if (main && main !== named(stored.gst_model)) {
    const memory = named(draft.memory_model);
    const followers = [
      !memory && "Extract facts",
      !named(draft.summarize_model) && !memory && "Condense story",
      !memory && "Suggest from facts",
      !named(draft.research_model) && "Web research",
      !named(draft.judge_model) && draft.judge_enabled === true && "LLM judge",
    ].filter((name): name is string => typeof name === "string");
    if (followers.length > 0) {
      const one = followers.length === 1;
      out.push(`${joined(followers)} ${one ? "has" : "have"} no model of ${one ? "its" : "their"} own and will also run on ${main}.`);
    }
  }
  const strong = named(draft.gst_fallback_model);
  if (strong && strong === main) {
    out.push("The Strong model is the same as the Default model. Saving clears it, and the translation runs on one model.");
  }
  return out;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

export interface LanguageEntry {
  code: string;
  language: string;
  enabled: boolean;
}

/** Each kind of key the Connections group shows, with the field the API stores it in and the kind `test-connection` takes. */
export const KEY_SLOTS: Array<{ field: string; label: string; kind: string }> = [
  { field: "bazarr_api_key", label: "Bazarr", kind: "bazarr" },
  { field: "tmdb_api_key", label: "TMDB", kind: "tmdb_api_key" },
  { field: "gemini_api_key", label: "Gemini 1", kind: "gemini_api_key" },
  { field: "gemini_api_key2", label: "Gemini 2", kind: "gemini_api_key2" },
  { field: "openai_compat_api_key", label: "OpenAI-compatible", kind: "openai_compat_api_key" },
];
