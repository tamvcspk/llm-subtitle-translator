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
- `store.py`: SQLite (`app.db`, versioned migrations) with the `translations`
  and `llm_calls` tables;
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
model after a provider outage. A manual retry clears the retry fields. An
optional `trigger` (`auto` by default, or `retranslate`) lets an explicit
retranslation overwrite an existing target after backing it up.

## Translation Flow

1. The worker picks the oldest pending job.
2. Existing output files are skipped to avoid overwriting subtitles (automatic
   jobs only; see the version rules above).
3. TMDB context is built when `TMDB_API_KEY` is available.
4. If the store already holds a result for the same source sha256, target
   language, and context hash (model, temperature, prompt version, TMDB
   description), the stored copy is published and no model call is made.
5. Otherwise `subcore` translates with the model the queue chose for this
   attempt: the primary `gst_model`, or `gst_fallback_model` when the job was
   handed over or the primary model's daily quota is exhausted. Every call goes
   through a rate limiter (RPM/TPM/RPD) and retries `429`/`503` with
   exponential backoff, jitter, and the server's `retry-after`. Calls time out
   after 300 seconds and a job has a 30 minute watchdog (`JOB_TIMEOUT_SECONDS`).
6. Provider failures arrive as typed exceptions, not parsed text. A `503` or
   rate limit that outlasts the in-process retries moves the job to `deferred`
   for 5, 15, 30, then 60 minutes. While a job waits out an outage, no other job
   starts, so a queue of jobs does not spend daily quota on the same errors.
   After four delayed retries the job moves to the fallback model once; without
   a fallback, or when the fallback is also unavailable, it moves to `failed`.
   Output that stays invalid after repair and splitting, a rejected API key, or
   an exceeded watchdog fails the job immediately.
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
