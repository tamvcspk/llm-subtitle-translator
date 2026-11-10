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
- `translation_attempt.py`: `TranslationAttempt`, the observation and execution
  seam for unfinished translation work, checkpoints, and publication;
- `translation.py`: translation execution and the `gst` work-file protocol,
  including checkpoint interpretation, partial-output cleanup, and publication;
- `tmdb.py`: TMDB lookup and translation description generation;
- `bazarr.py`: Bazarr API refresh, wanted-item lookup, and API-key parsing;
- `backups.py`: server-side backup listing and creation;
- `logs.py`: logging setup, UTC formatting, 5 MiB rotation (three backups),
  bounded event snapshots, and clearing the current log;
- `http.py`: small HTTP client and JSON file cache helpers.

The worker calls the `gst` CLI from `gemini-srt-translator`, writes the target
subtitle, and asks Bazarr to scan the affected series or movie.

`QueueWorker` binds `JobQueue.process_once` to the configured translation
workflow through an execution callback and exposes `ConsoleActions` to the HTTP
adapter. Queue lifecycle tests use the same
interface with a callback and a real temporary directory, without HTTP or TMDB
setup. Recovery is explicit at worker startup, so creating a queue handle does
not recover work that is still running.

Queue snapshots obtain translation progress through the `TranslationAttempt`
interface;
they do not interpret translator filenames or checkpoint fields. Execution and
observation share the translation module's work-file implementation. A visible
checkpoint does not by itself imply resumability: partial output must also exist.

`RuntimeContext` is the composition seam for the running process. It carries the
queue console actions, Bazarr integration, backup maintenance, and HTTP transport
adapters alongside the worker's shared settings and state paths. The legacy
module-level functions remain compatibility entry points for direct callers and
older tests; new runtime composition should depend on the typed context fields.

The direct seam tests in `tests/test_architecture_modules.py` verify policy,
console actions, and translation-attempt delegation independently of the HTTP
server and the complete queue lifecycle tests.

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
model after a provider outage. A manual retry clears the retry fields.

## Translation Flow

1. The worker picks the oldest pending job.
2. Existing output files are skipped to avoid overwriting subtitles.
3. TMDB context is built when `TMDB_API_KEY` is available.
4. The worker executes `gst translate` with the model the queue chose for this
   attempt: the primary `gst_model`, or `gst_fallback_model` when the job was
   handed over or the primary model's daily quota is exhausted.
5. A Gemini `503` moves the job to `deferred` for 5, 15, 30, then 60 minutes.
   This includes `gst` stopping at its own consecutive-error limit when the last
   error was a `503`, because an outage outlasts its short internal retries.
   While a job waits out an outage, no other job starts, so a queue of jobs does
   not spend daily quota on the same errors. After four delayed retries the job
   moves to the fallback model once, resuming from its checkpoint; without a
   fallback, or when the fallback is also unavailable, it moves to `failed`.
   If `gst` stops at its retry limit on any other last error, such as invalid
   content, the job fails immediately without queue retries so nested retries do
   not multiply provider requests. The worker log retains a redacted failure
   diagnostic even if the failed job is cancelled.
6. A daily quota `429` pauses that model for 24 hours. If another configured
   model still has quota, the job returns to `pending` and continues on it.
   Once every configured model is exhausted, the job and waiting jobs move to
   `failed`, automatic retries are cancelled, and admission and manual retries
   are blocked until the first model's pause expires, or until the settings
   name a model whose quota is not exhausted.
   Content line-count errors alone retry with the smaller batch size.
7. On success, the job moves to `done`.
8. The worker refreshes Bazarr with `scan-disk`; if item IDs are missing, it
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
