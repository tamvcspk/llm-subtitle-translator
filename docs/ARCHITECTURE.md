# Architecture

> Describes the current (inherited) worker. The repository is a uv workspace:
> worker paths below (`worker.py`, `gst_worker/`, `static/`, `tests/`) live under
> `apps/worker/`; the new engine package is `packages/subcore/`. The image keeps
> the old runtime layout (`/app/worker.py`, `/app/gst_worker`, `/app/static`).
> Target design: [design/ARCHITECTURE.md](design/ARCHITECTURE.md).

## Components

### Bazarr Post-Processing Script

`bazarr-postprocess/gst_enqueue.sh` runs inside the Bazarr container after Bazarr
downloads or extracts a subtitle. The script is intentionally small:

- validate provider and source language;
- load source and target languages from `targets.json`;
- skip targets that already have output files;
- write one JSON job per missing target into the shared queue.

It does not call Gemini and should return quickly.

### Worker

`worker.py` is the runtime entrypoint. In normal mode it starts:

- a background worker loop that consumes one pending job at a time;
- a small stdlib HTTP server for the console and JSON API.

Domain code is split under `gst_worker/`:

- `config.py`: app settings, language normalization, and Bazarr language list
  loading;
- `subtitles.py`: subtitle path mapping and local source subtitle scanning;
- `queue.py`: queue admission, lifecycle, skip checks, and snapshots. `JobQueue`
  owns recovery, settling, claiming, deferred retries, quota pauses, and status
  persistence. Console retry and delete operations share its transition helpers;
- `queue_policy.py`: pure retry and daily quota lifecycle decisions. This is the
  policy seam; filesystem moves remain in `queue.py`.
- `console.py`: `ConsoleActions`, the small interface used by the HTTP adapter
  for queue actions. It keeps request routing separate from queue orchestration;
- `translation_attempt.py`: `TranslationAttempt`, the execution seam for one
  translation job: it reads the source, calls `subcore` in-process, maps typed
  provider errors to the queue's exceptions, and publishes the result;
- `translation.py`: the two failure classes the queue acts on
  (`ProviderUnavailableError`, `DailyQuotaExceededError`) and secret redaction;
- `llm.py`: builds the provider for an attempt from settings: one rate-limited
  Gemini provider per API key, with failover to the next key;
- `store.py`: SQLite (`app.db`, versioned migrations) with the `translations`,
  `llm_calls`, library, `facts`, `summaries_cache` and `context_snapshots` tables;
- `memory.py`: `StoryMemory`, the worker side of story memory: story order,
  prerequisite fact extraction, gap waiting, the context bundle and its staleness;
- `versions.py`: `TranslationVersions`, publishing with version history, backup
  before overwrite, and revert;
- `tmdb.py`: TMDB lookup and translation description generation;
- `bazarr.py`: Bazarr API refresh, wanted-item lookup, and API-key parsing;
- `backups.py`: server-side backup listing and creation;
- `logs.py`: logging setup, UTC formatting, 5 MiB rotation (three backups),
  bounded event snapshots, and clearing the current log;
- `http.py`: small HTTP client and JSON file cache helpers.

The worker translates in-process with the `subcore` engine (workspace package
`packages/subcore`), writes the target subtitle, and asks Bazarr to scan the
affected series or movie. There is no subprocess and no `gst` CLI.

`QueueWorker` binds `JobQueue.process_once` to the configured translation
workflow through an execution callback and exposes `ConsoleActions` to the HTTP
adapter. Queue lifecycle tests use the same
interface with a callback and a real temporary directory, without HTTP or TMDB
setup. Recovery is explicit at worker startup, so creating a queue handle does
not recover work that is still running.

Translation progress is reported by the attempt through the queue's status
callback and stored in the processing job (`stage`, `progress_done`,
`progress_total`); queue snapshots just read the job file. A failed or
interrupted attempt starts again from the beginning: only a finished result is
kept, so there are no work files to resume from.

The SQLite store (`DATA_DIR/app.db`, default `STATE_DIR`) holds machine-owned
state. `translations` keeps every version published for a target path with its
sha256, input hash, and a copy under `DATA_DIR/backups/subtitles/`; `llm_calls`
records every model attempt (tokens, latency, outcome). The queue itself is
still the JSON file layout below; moving it into a `jobs` table is a later step.

A target file is *system-made* when the store's current version for its path has
the file's sha256. An automatic job never overwrites any existing target. A
retranslate or revert first keeps a file that is not the current version (a hand
edit, or a file someone else put there) as an `external` version, so nothing is
lost, then writes atomically. Reverting restores a stored version byte for byte
and records the restore as a new version.

### Library, corrections and retranslation (P3)

A *unit* (an episode or a movie) is registered the first time a job for it runs
(`library.py`; identity from Bazarr ids, then the file name, with best-effort
enrichment from the Bazarr API). `units` and `series` rows, a
`unit_outputs(unit, language) → path` mapping, `corrections`, and a `unit_id` plus
`glossary_hash` on every translation version live in `app.db` (schema 2; schema 3
adds story memory, below).

`rules.py` merges the series glossary (`DATA_DIR/series/<key>/glossary.<lang>.yaml`,
read by `glossaries.py`) with the unit's active episode corrections for one
episode. Terms and address rules carry `from`/`until` windows. The result goes to
the prompt, and its hash is stored as the version's `glossary_hash`: when the hash
computed now differs, the translation is *stale* (flagged only). Hand-edited cues
(`cue_edit`) are outside the hash; they are locked output.

`units.py` (`UnitService`) implements the console actions: cue view, corrections,
retranslation, versions, diff, revert, glossary read/write. `library_api.py` routes
`/api/library/*`, `/api/units/*` and `/api/series/*` to it. A retranslation is a
queue job (`trigger: retranslate`, `priority: 100`, `retranslate.range`,
`retranslate.correction_ids`); the attempt asks `subcore` for the range only
(`only=`), passing locked cues and the current translation of cues outside the
range as `keep=`, which are copied verbatim and shown to the model as reference
translations.

### Story memory (P4)

`subcore.memory` is the pure part: `facts.py` reads the *facts* of one unit from its
source subtitle (characters, relationship changes with the cue where they show, terms,
a summary; structured output, nothing but the numbered text is sent), `fold.py` folds the
facts of the units before N into the *story state @N* and renders the prompt sections
("Story so far", "Relationship state", "This episode"), `compress.py` condenses old
summaries with one model call. No queue, no Bazarr, no filesystem layout.

`memory.py` (`StoryMemory`) is the worker's side. Story order is `(season, episode)` as
Bazarr numbers them; season 0 is outside the chain and sees the state after
`series.specials_after` (default: the end of the first season). Before a unit is
translated, `plan()`:

1. asks Bazarr for the series' episodes and their source subtitles (`list_series_episodes`,
   best effort) and registers them as units;
2. for each earlier unit, reads its facts if its source exists and none are stored for
   that sha256 + `EXTRACT_PROMPT_VERSION` (`facts` table), keeping stored facts when the
   source has since gone; a unit with neither a source nor facts is a *gap*;
3. while there is a gap and `memory_gap_wait_seconds` has not passed, raises
   `ContextGapError`; the queue defers the job (`deferred_reason: context-gap`, `gap_since`
   in the job file, rechecked every 5 minutes). After the wait, or for a retranslation, it
   translates with `context_complete = 0` and the missing labels stored;
4. builds the bundle; if the story exceeds `memory_budget_tokens`, the older summaries
   are condensed by one `summarize` call cached in `summaries_cache`.

Prerequisites run inline in the same attempt (one job at a time, ADR-4), not as separate
`extract` queue jobs. The bundle's *fingerprint* (a hash of the label and content hash of
every earlier unit's facts plus the unit's own) is stored as `translations.memory_hash`
and the rendered text in `context_snapshots`. `Library.target_info` marks a version
`stale` when the fingerprint computed from the stored facts now differs, or when a unit
listed in its `missing_units_json` has a source subtitle now; both facts-per-unit lookups
read "the newest stored facts for the current extraction prompt version", so building a
bundle and checking staleness can never disagree. `TranslationAttempt` takes the extra
sections from the plan (after the glossary and the episode corrections) and maps provider
errors raised while extracting exactly as for translating, so a spent daily quota pauses
the queue and the facts already read are kept.

`RuntimeContext` is the composition seam for the running process. It carries the
queue console actions, Bazarr integration, backup maintenance, and HTTP transport
adapters alongside the worker's shared settings and state paths. The legacy
module-level functions remain compatibility entry points for direct callers and
older tests; new runtime composition should depend on the typed context fields.

The direct seam tests in `tests/test_architecture_modules.py` verify policy and
console actions, and `tests/test_translation_attempt.py` runs the translation
attempt against fake providers, independently of the HTTP server and the
complete queue lifecycle tests.

Bazarr remains a separate, dependency-free file producer. Its embedded Python
payload is tested against the worker's admission and retry protocol; it does not
need network access to enqueue work.

### Web Console

`static/` contains a dependency-free HTML/CSS/JavaScript UI. It calls the local
worker API only; it does not talk directly to Bazarr, Gemini, or TMDB.

There is no build step: the browser loads native ES modules from `static/js/`,
so the worker serves `.js` as `text/javascript` with `Cache-Control: no-cache`.

- `index.html` holds the page shell and the static markup for every view.
  Settings inputs are named after the `POST /api/settings` fields.
- `js/main.js` owns hash routing (`#queue/failed`), the shared five-second poll,
  sidebar badges and health, the quota banner, and the theme toggle.
- `js/store.js` caches `/api/queue`, `/api/status`, languages, and models, and
  notifies subscribers so one poll updates every visible part of the page.
- `js/views/*.js` export one object per view with `title`, `subtitle`, and
  optional `init()`, `enter(param)`, `leave()`, and `tick()` hooks.
- `js/ui.js` builds DOM through text nodes only; paths and provider errors are
  untrusted and must never be written as HTML.

## Queue Layout

The queue is a set of JSON files in five directories:

```text
queue/
  pending/
  processing/
  deferred/
  done/
  failed/
```

A job file is moved between directories as state changes. Deferred and failed
jobs may also have a sibling `.error` file containing the exception text.
Deferred jobs record `retry_at`. `provider-pause.json` records which models have
exhausted their daily quota (`models`, model name to expiry) and, once every
configured model has, the global circuit breaker (`reason`, `retry_at`) that
prevents other work from consuming requests. The worker re-derives the circuit
breaker from `models` whenever settings change and before each claim, so
switching to a model that still has quota lifts it.

## Job Shape

```json
{
  "job_id": "sha1",
  "video_path": "/media/Show/Episode.mkv",
  "subtitle_path": "/media/Show/Episode.en.srt",
  "output_path": "/media/Show/Episode.zh.srt",
  "source_code": "ja",
  "source_language": "Japanese",
  "target_code": "zh",
  "target_language": "Simplified Chinese",
  "provider": "embeddedsubtitles",
  "media_type": "series",
  "series_id": "144",
  "media_id": "14672"
}
```

The queue adds runtime fields: `gst_model` is the model chosen for the latest
translation attempt, and `use_fallback_model` marks a job handed to the fallback
model after a provider outage or a hard job (`escalated_from` and
`escalation_reason` then say why). A finished job also records its `result`
(`translated`, `skipped`, `skipped-not-wanted`, ...). A manual retry clears the
retry and escalation fields. An
optional `trigger` (`auto` by default, or `retranslate`) lets an explicit
retranslation overwrite an existing target after backing it up.

## Translation Flow

1. The worker picks the oldest pending job.
2. The job is dropped, with no model call, unless its language is still missing
   (automatic jobs only; see the version rules above): the exact target file or
   any sibling subtitle in that language exists, the folder is outside
   `GST_ONLY_PATHS`, or Bazarr no longer lists the language in the item's
   `missing_subtitles` (`skipped-not-wanted`; if Bazarr cannot answer, the files
   decide). The same checks run when the job is queued, so most never enter the
   queue; `GST_MAX_PENDING` caps how many can.
3. TMDB context is built when `TMDB_API_KEY` is available.
4. If the store already holds a result for the same source sha256, target
   language, and context hash (model, temperature, prompt version, TMDB
   description), the stored copy is published and no model call is made.
5. Otherwise `subcore` translates with the model the queue chose for this
   attempt: the default `gst_model` (a light model), or the strong model
   `gst_fallback_model` when the job was handed over (hard job or outage) or the
   default model's daily quota is exhausted. Every call goes
   through a rate limiter (RPM/TPM/RPD) and retries `429`/`503` with
   exponential backoff, jitter, and the server's `retry-after`. Calls time out
   after 300 seconds and a job has a 30 minute watchdog (`JOB_TIMEOUT_SECONDS`).
6. Provider failures arrive as typed exceptions, not parsed text. A `503` or
   rate limit that outlasts the in-process retries moves the job to `deferred`
   for 5, 15, 30, then 60 minutes. While a job waits out an outage, no other job
   starts, so a queue of jobs does not spend daily quota on the same errors.
   After four delayed retries the job moves to the fallback model once; without
   a fallback, or when the fallback is also unavailable, it moves to `failed`.
   A rejected API key or an exceeded watchdog fails the job immediately. Output
   that stays invalid after repair and splitting (`HardJobError`) is a hard job:
   the default model is a light one, so the job moves once to the strong model
   (`gst_fallback_model`), recording `escalated_from`; if that also fails, or
   there is no strong model, the job fails. `layer_report` (`/api/status`
   `model_layers`) counts how many jobs escalate and how many the strong model
   finishes, to decide whether the layering is worth keeping.
7. A daily quota error pauses that model for 24 hours. If another configured
   model still has quota, the job returns to `pending` and continues on it.
   Once every configured model is exhausted, the job and waiting jobs move to
   `failed`, automatic retries are cancelled, and admission and manual retries
   are blocked until the first model's pause expires, or until the settings
   name a model whose quota is not exhausted.
8. On success the result is written atomically, recorded as a version, and the
   job moves to `done`.
9. The worker refreshes Bazarr with `scan-disk`; if item IDs are missing, it
   falls back to full subtitle scan tasks.

## Path Model

Bazarr, the post-processing script, and the worker must agree on media paths.
The recommended Docker model is:

```yaml
volumes:
  - /host/media:/media
```

If Bazarr sees a subtitle as `/media/Movie/Movie.en.srt`, this worker must be
able to read and write that exact path inside its container.
