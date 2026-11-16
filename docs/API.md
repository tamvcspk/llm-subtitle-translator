# HTTP API

The web console uses these endpoints. They are intentionally small and local to
the worker; do not expose them directly to the internet without a trusted
reverse proxy that adds TLS.

## Authentication

Every endpoint, and every static file, needs the console account, either as a
login-form session cookie (`gst_session`, browsers) or as HTTP Basic Auth
(`Authorization: Basic base64(user:password)`, scripts). The only requests that
need nothing are `GET /login` and `POST /api/auth/login`. See
[CONFIGURATION.md](CONFIGURATION.md#console-authentication).

| Situation | Response |
| --- | --- |
| Browser page request without a session | `302` to `/login` |
| Browser API call without a session | `401` `{"error": "authentication required"}`, no `WWW-Authenticate` |
| Script without or with wrong Basic credentials | `401` with `WWW-Authenticate: Basic realm="LLM Subtitle Translator"` |
| 5 wrong logins in a row from one address (form, Basic, or password change) | `429` with `Retry-After` (5 minutes) |
| Write (`POST`/`PUT`/`DELETE`) with a cookie and a foreign `Origin` | `403` |
| No account yet (`CONSOLE_USER` / `CONSOLE_PASSWORD` never set) | `503` `{"error": "..."}` with setup instructions |
| `CONSOLE_AUTH=off` | No check |

```bash
curl -u "$CONSOLE_USER:$CONSOLE_PASSWORD" http://<host>:6789/api/status
```

### POST /api/auth/login

`{"user": "...", "password": "..."}` → `200 {"ok": true, "user": "..."}` and a
`Set-Cookie: gst_session=...; HttpOnly; SameSite=Strict; Max-Age=604800` (plus
`Secure` when the request carries `X-Forwarded-Proto: https`). Wrong
credentials: `401`, no cookie. A session expires after 7 days without use.

### POST /api/auth/logout

Ends the caller's session and clears the cookie. Always `200`.

### GET /api/auth/me

`{"user": "admin" | null, "via": "session" | "basic" | "off", "can_change_password": true}`.

### POST /api/auth/password

`{"current": "...", "new": "..."}`. The new password must be 8–256 characters.
`200 {"ok": true}` ends every session; a caller using a cookie gets a fresh one
and stays signed in. A wrong current password is `403` (not `401`, so the
console does not take it for a dead session) and counts towards the lockout;
a bad new password is `400`; with `CONSOLE_AUTH=off` it is `409`.

## GET /api/status

Returns queue counts, current settings, Bazarr URL, and Bazarr ping status.

```json
{
  "queue": {"pending": 0, "processing": 0, "done": 1, "failed": 0},
  "settings": {
    "source_languages": [
      {"code": "en", "language": "English", "enabled": true}
    ],
    "target_languages": [
      {"code": "zh", "language": "Simplified Chinese", "enabled": true}
    ],
    "media_roots": ["/media"],
    "scan_limit": 200,
    "bazarr_url": "http://bazarr:6767",
    "bazarr_api_key": "**********",
    "bazarr_api_key_configured": true,
    "bazarr_api_key_last4": "",
    "bazarr_api_key_status": "ok",
    "gemini_api_key": "**********",
    "gemini_api_key_configured": true,
    "gemini_api_key_last4": "Lq3k",
    "gemini_api_key_status": "ok",
    "gemini_api_key2": "",
    "gemini_api_key2_configured": false,
    "gemini_api_key2_last4": "",
    "gemini_api_key2_status": "",
    "tmdb_api_key": "**********",
    "tmdb_api_key_configured": true,
    "tmdb_api_key_last4": "",
    "tmdb_api_key_status": "ok",
    "openai_compat_api_key": "",
    "openai_compat_api_key_configured": false,
    "openai_compat_api_key_last4": "",
    "openai_compat_api_key_status": "",
    "llm_provider": "gemini",
    "llm_base_url": "",
    "key_warnings": []
  },
  "bazarr_url": "http://bazarr:6767",
  "bazarr": {"status": "OK"},
  "model_layers": {
    "by_model": {
      "gemini-3.1-flash-lite": {"done": 41, "failed": 1},
      "gemini-3.5-flash": {"done": 3, "failed": 1}
    },
    "escalated": {"total": 5, "done": 3, "failed": 1, "waiting": 1},
    "window": {"done_days": 30, "failed_days": 90}
  }
}
```

`model_layers` is read from the job rows and shows whether the strong model is
worth having. File cleanup deletes finished jobs past `retention_done_days` and
`retention_failed_days`, so the counts reach back only that far: `window` says how far
(`0` means that kind is never deleted). `by_model` counts translated and failed jobs per model that ran
them last; `escalated` counts jobs the default model could not finish (output
still invalid after repair) that moved to the strong model, split by how they
ended. Few escalations, or a strong model that rescues few of them, means the
second layer is not paying for itself.

## GET /api/queue

Returns all queue states and counts. The states are `pending` (queued), `processing`
(running), `deferred` (waiting, see `wait_reason`), `done` (includes `skipped`) and `failed`;
each list is the latest 100 jobs of its state, `counts` has the real numbers.

Each waiting job carries `wait_reason` and `retry_at` (Unix seconds, when it may run
again). The reasons are `quota` (daily quota spent), `provider` (key refused, model
unknown, or breaker open), `context` (earlier episodes' facts missing; `missing_units`
names them), `settle` (source subtitle changed a moment ago) and `rate` (provider busy,
retrying soon). A wait is never a failure. A running job carries `stage`, and
`progress_done` / `progress_total` while it translates. `attempts` counts restarts after
a crash; a job that crashed `max_crashes` times is `failed` with `error` starting
`crashed:`.

The response also has `providers`: one entry per configured model with `model`,
`state` (`closed`, `open` (breaker), `exhausted` (quota), `invalid` (key or model refused),
or `half_open`), `reason`, `until` (when it may be tried again, or null), `failures`
(consecutive failures) and `usable`.

The same `providers` list is in `/api/status`.

## POST /api/queue/stop

`{"job_id": "..."}`. A waiting or queued job is cancelled at once. A running job is
asked to stop and does so at the next batch boundary, so a subtitle being written is
finished first. Returns `{"ok": true, "outcome": "cancelled"}` or
`{"ok": true, "outcome": "stopping"}`; `ok` is false when there was nothing to stop.

## POST /api/queue/request-prerequisites

`{"job_id": "..."}` for a job with `wait_reason` `context`. Queues the earlier episodes the
job waits for, when their source subtitle is on disk, at interactive priority. Returns
`{"requested": ["<job id>"], "unavailable": ["S01E02"]}`; `unavailable` names the earlier
episodes that have no source subtitle yet. Asking again makes no second job. Returns
`400` for a job that is not waiting for earlier episodes.

Both `/api/queue` and `/api/status` include `daily_quota_pause`: `null` when
inactive, or `{"retry_at": 1790355039.786}` while paused (Unix seconds).
The console shows the expiry in the browser's local time and explains that
cancelling failed jobs does not lift the pause. Provider error details are not
included in this field.

## GET /api/jobs/{id}/spec

The bundle (see CONTEXT.md, *Bundle*) that the job's latest translation attempt used, as the JSON document
`subcore translate --bundle` reads. It holds the cues, the episode's glossary, the sections, the parameters and
no key or machine path (a key that reached the text is masked before translation, so the spec reproduces the run). `404` when the job has not translated
yet (`{"error": "..."}`) or the id is malformed. Requires the same login as every `/api` route.

## GET /api/settings

Returns the current settings, read from the database (a change saved a moment ago is already in it).
Each of the five key fields (`bazarr_api_key`, `tmdb_api_key`, `gemini_api_key`, `gemini_api_key2`,
`openai_compat_api_key`) is
returned as `**********` when a key is stored, with:

- `<field>_configured`: whether a key is available;
- `<field>_last4`: the last four characters of the stored key, `""` when there is none or it is shorter than 12
  characters (a value is never returned);
- `<field>_status`: `ok`, `invalid` (the provider refused it) or `exhausted`; `""` when no key is stored.

`key_warnings` lists what Settings must warn about, each `{"kind", "message", ...}`: `key-invalid` (with
`provider`, `key_id`, `label`) for a refused key, `model-not-found` (with `model`) for a model the provider
does not know, `provider-unconfigured` when `llm_provider` is `openai_compat` and `llm_base_url` is empty.
Empty when all is well.

## GET /api/languages

Returns Bazarr-supported language choices normalized for the web console.

```json
{
  "items": [
    {
      "code": "zh",
      "code3": "zho",
      "name": "Chinese Simplified",
      "language": "Simplified Chinese",
      "enabled_in_bazarr": true
    }
  ]
}
```

## GET /api/gemini-models

Returns the model choices for the Settings dropdown (the name is from before the second provider). With
`llm_provider` `gemini`: if the Gemini API cannot be reached or no key is configured, the worker returns a bundled
fallback list. With `openai_compat`: the models the server lists at `{llm_base_url}/models` (asked with the stored
key, if any), or an empty list when the address is unset or the server cannot be reached.

```json
{
  "items": [
    {"id": "gemini-3.1-flash-lite", "name": "gemini-3.1-flash-lite"},
    {"id": "gemini-3.5-flash", "name": "gemini-3.5-flash"}
  ]
}
```

## POST /api/settings

Saves app settings to the database. The change is live at once, with no restart: the running worker adopts it and the answer
is the new `GET /api/settings`. Leave a key field blank or submit `**********` to keep the stored key; a new
value replaces the key in that field and sets its status back to `ok`. Keys that are not in the body, and
process-level settings (paths, port, console account), are ignored. Use `/api/keys` to add, rename or remove
keys beyond these four fields.

```json
{
  "bazarr_url": "http://bazarr:6767",
  "bazarr_api_key": "",
  "gemini_api_key": "",
  "gemini_api_key2": "",
  "tmdb_api_key": "",
  "gst_model": "gemini-3.1-flash-lite",
  "gst_fallback_model": "gemini-3.5-flash",
  "gst_paid_quota": false,
  "gst_temperature": "0.7",
  "gst_thinking_budget": "2048",
  "gst_no_thinking": false,
  "llm_provider": "gemini",
  "llm_base_url": "",
  "llm_max_input_tokens": 128000,
  "llm_max_output_tokens": 8192,
  "research_model": "",
  "openai_compat_api_key": "",
  "job_settle_seconds": 600,
  "auto_request": false,
  "auto_request_daily_cap": 0,
  "catalog_sync_interval": 300,
  "catalog_shrink_guard": 20,
  "memory_enabled": false,
  "memory_model": "",
  "summarize_model": "",
  "memory_gap_wait_seconds": 1800,
  "memory_budget_tokens": 8000,
  "memory_recent_episodes": 3,
  "log_level": "INFO",
  "log_prompt_bodies": "all",
  "log_body_max_bytes": 262144,
  "log_max_mb": 500,
  "log_retention_days": 30,
  "events_retention_days": 90,
  "llm_cost_in_per_mtok": 0.0,
  "llm_cost_out_per_mtok": 0.0,
  "source_languages": [
    {"code": "en", "language": "English", "enabled": true},
    {"code": "ja", "language": "Japanese", "enabled": true}
  ],
  "target_languages": [
    {"code": "zh", "language": "Simplified Chinese", "enabled": true},
    {"code": "en", "language": "English", "enabled": true}
  ],
  "media_roots": ["/media"],
  "scan_limit": 200
}
```

## GET /api/wanted

Reads Bazarr wanted series and movies, then returns items that can be translated
from an existing configured source subtitle.

## GET /api/scan

Scans configured media roots for local source subtitle files with missing target
outputs.

Optional query:

```text
/api/scan?limit=500
```

## POST /api/enqueue

Creates jobs for one item.

```json
{
  "video_path": "/media/Show/Episode.mkv",
  "subtitle_path": "/media/Show/Episode.ja.srt",
  "source_code": "ja",
  "source_language": "Japanese",
  "media_type": "series",
  "series_id": "144",
  "media_id": "14672",
  "target_codes": ["zh", "en"]
}
```

## POST /api/enqueue-scan

Scans media roots and enqueues all missing target jobs found by that scan.

## POST /api/queue/retry

Moves a failed job back to pending with a fresh retry cycle (waits, breaker and call counts
are reset). Returns `{"ok": false}` when the job is not failed or the same file already has an
open job.

```json
{"job_id": "7fefefb6fdb4f200bbb5fcac70e917492cea4d8f"}
```

## POST /api/queue/delete

Deletes one job from a queue state. Deleting a
`pending` or `deferred` job is a cancel: the record is kept as `cancelled`. A `processing`
job cannot be deleted; use `/api/queue/stop`.

```json
{"state": "failed", "job_id": "7fefefb6fdb4f200bbb5fcac70e917492cea4d8f"}
```

## GET /api/logs

Returns up to 200 recent events from the current worker log. Each `entries`
item has `timestamp`, `level`, `message`, and `details` (multiline tracebacks).
`truncated` indicates older content
was omitted. Reads are limited to the last 512 KiB of the file; filters and
export in the console apply only to this snapshot. Timestamps use UTC with an explicit `Z`.

## POST /api/logs/clear

Truncates the current worker log file under the writer lock. Rotated history files are preserved.

## GET /api/events

The event log: what the worker decided and why (skip a job, claim it, pick a model,
escalate, defer, publish a version, ...). Newest first.

Query (all optional):

| Parameter | Meaning |
|---|---|
| `job` | one job id |
| `unit` | one library unit id |
| `kind` | an exact kind (`job.skipped`), or a family when it ends with a dot (`job.`) |
| `level` | that level and worse (`INFO`, `WARNING`, `ERROR`) |
| `since` | epoch seconds; only newer events |
| `before` | an event id; only older events (the `next_before` of the previous page) |
| `limit` | 1-1000, default 100 |

```json
{
  "items": [
    {
      "id": 412, "at": 1763175010.4, "level": "INFO", "kind": "job.skipped",
      "job_id": "7fefefb6...", "unit_id": 12,
      "reason": "target subtitle already exists",
      "data": {"subtitle": "Episode.en.srt", "target": "vi"}
    }
  ],
  "next_before": 389,
  "telemetry": {"written": 5120, "dropped": 0, "queued": 0}
}
```

`next_before` is `null` on the last page. `telemetry.dropped` counts records that were not
written because the writer's queue was full or a write failed (the job is never affected).
`data` never holds an API key, password, cookie or header; the filter that removes them is
shared with the log. Without the database the answer is `{"items": [], "next_before": null,
"telemetry": null}`. A malformed number returns `400`. Event kinds are documented in
`docs/ARCHITECTURE.md` ("Logging and measurement").

## GET /api/backups

Lists server-side backup zip files under `/state/backups`.

## POST /api/backups

Creates a manual backup zip containing the saved settings and
metadata. **No API key is in the zip** unless the body is `{"include_keys": true}`, which adds
`config/api_keys.json` (provider, label, value, status of every key) and sets `includes_api_keys` in
`backup.json`. The console account is never included.

## GET /api/backups/download?name=<backup.zip>

Downloads a backup zip by file name. The `name` value must be a file listed by
`GET /api/backups`; path components are rejected.

## POST /api/backups/import

Imports a backup zip sent as the raw request body with
`Content-Type: application/zip`. The import restores:

- `config/config.json` into the saved settings (the running worker adopts them at once);
- `config/api_keys.json`, when the backup has it: its keys are added (a key already stored is not duplicated);
- `postprocess/targets.json` (a leftover of Bazarr's old script) to the worker's own copy of it.

The console account is never restored. The worker creates a `pre-import` backup (without keys) before
replacing anything. Queue state, logs, and caches are not restored. A backup member that is not valid JSON
refuses the whole import with `400` before anything changes.

## POST /api/cleanup

File cleanup by the `retention_*` settings (FR-13; see "File Clean-up" in `docs/CONFIGURATION.md`). Body
`{"dry_run": true}` or `{"dry_run": false}`. **It is a preview unless the body says `"dry_run": false`**: an empty
body, `true`, `"false"` or anything else deletes nothing. Reply:

```json
{
  "dry_run": true,
  "at": 1784000000.0,
  "groups": [
    {"name": "jobs_done", "enabled": true, "window": 30, "files": 0, "rows": 412, "bytes": 1832040, "error": ""},
    {"name": "jobs_failed", "enabled": true, "window": 90, "files": 0, "rows": 3, "bytes": 8120, "error": ""},
    {"name": "versions", "enabled": true, "window": 5, "files": 87, "rows": 0, "bytes": 5402112, "error": ""},
    {"name": "glossary", "enabled": true, "window": 20, "files": 0, "rows": 6, "bytes": 3190, "error": ""},
    {"name": "temp", "enabled": true, "window": null, "files": 1, "rows": 0, "bytes": 2048, "error": ""}
  ],
  "total": {"files": 88, "rows": 415, "bytes": 7244320},
  "media_temp": ["/media/Show/.Ep1.vi.srt.publish.tmp"]
}
```

`window` is the setting of the group (days, or how many copies to keep; `null` for temporary files), and `enabled` is
`false` when it is `0`. `files` are files on disk, `rows` are database rows (finished jobs), `bytes` is what they hold.
A dry run and the real run right after it report the same numbers. `error` is set when one group failed; the others
still ran. `media_temp` lists leftover temporary files beside subtitles in media folders: they are never deleted.
The same cleanup runs by itself once a day.

## POST /api/test-connection

Tests a configured or form-entered API key without saving it. `kind` can be
`bazarr`, `gemini_api_key`, `gemini_api_key2`, `openai_compat_api_key` (asks `{llm_base_url}/models`; a server
that needs no key may be tested with the field blank) or `tmdb_api_key`. When the key field is blank or `**********`
the **stored** key is tested (even one marked `invalid`) and its status follows the answer: `ok` when it works,
`invalid` when the service refuses it; an unreachable service changes nothing. A typed key is only tried.

```json
{
  "kind": "gemini_api_key",
  "bazarr_url": "http://bazarr:6767",
  "gemini_api_key": "**********"
}
```

Returns:

```json
{"ok": true, "kind": "gemini_api_key", "message": "Gemini API key is valid"}
```

A failure carries `"ok": false`, a `message`, and `"auth_failed": true` when the service refused the key.

## API keys: /api/keys

Every stored key (FR-20.2). The answer never holds a value, only its last four characters.

A key is `{"id", "provider", "label", "status", "last4", "last_used_at", "updated_at"}` with `provider` one of
`gemini`, `openai_compat`, `bazarr`, `tmdb`; `status` is `ok`, `invalid` or `exhausted`; `last4` is `""` for a key shorter than
12 characters; `last_used_at` is Unix seconds or `null`.

| Route | Body | Answer |
| --- | --- | --- |
| `GET /api/keys` | | `{"items": [key, ...], "warnings": [...]}` (same warnings as `key_warnings` of `/api/settings`) |
| `POST /api/keys` | `{"provider", "value", "label"?}` | the new key; `400` for an unknown provider or an empty value |
| `PUT /api/keys/{id}` (or `POST`) | any of `{"label", "value", "status"}` | the key; a new `value` sets the status back to `ok` unless `status` is also sent; `404` for an unknown id, `400` for a bad status |
| `POST /api/keys/{id}/test` | | `{"ok", "message", "auth_failed"?, "key": key}`; a key that works becomes `ok`, one the service refuses becomes `invalid` |
| `POST /api/keys/{id}/remove` | | `{"ok": true}` or `404` |

The worker runs on the first two usable (`ok`) Gemini keys, oldest first, or, with `llm_provider` `openai_compat`,
on every usable key of that provider in the order they were added (none is needed for a server that wants no
key). Every change reaches the running worker at once.

## The catalog: what Bazarr lists, and requests

The worker pulls the library from Bazarr and keeps it as a cache (`catalog_items`); the Library page browses it.
An item is a *series*, an *episode* or a *movie*, addressed by its catalog `id` (Bazarr's own id is in
`bazarr_id`). Errors are `{"error": "..."}` with `400` or `404`.

The state of each (item, language) is **derived when read**, from the catalog, the version history, the jobs
and the decisions. A target comes as
`{"lang", "content", "removed", "stale", "job", "wait", "ignored", "text"}`: `content` is `missing`,
`translated`, `edited`, `external` (a subtitle the worker did not make) or `outside` (the language is not in
Bazarr's profile for the item; it can still be requested); `job` is `requested`, `running`, `waiting` (with
`wait`: `quota`, `provider`, `context`, `settle`, `rate`), `failed` or `""`; `text` is the label as the console
shows it, such as `Missing · Requested`, `Translated · Stale` or `Missing · Removed · Ignored`.

| Method and path | Answer |
|---|---|
| `GET /api/catalog` | `{"status", "languages", "series": [{"id", "title", "key", "episodes", "missing": {"vi": 3}, "open_jobs": {"requested": 1}, "failed": {"vi": 2}, "ignored": {"vi": 1}, "auto_request", "series_id"}], "movies": {"count", "missing", "failed", "ignored"}}`. Counts come from the catalog, the jobs and the decisions in a few queries (no per-item work). `failed` and `ignored` are per target language and equal what the `Failed` and `Ignored` labels of the series page (or the movies page) add up to: `failed` counts units with no open job whose newest finished job for that language is `failed` (so a failure asked for again, or followed by a success, is not counted); `ignored` counts the "do not translate" decisions. They feed the Failed and Ignored chips of Home. |
| `GET /api/catalog/status` | `{"synced_at", "attempted_at", "status": "ok" \| "kept" \| "", "warning", "items", "listed", "auto_request", "auto_request_daily_cap"}`. `synced_at` is the last accepted read; `status: "kept"` means the last read was set aside and `warning` says why (Bazarr unreachable, empty, or shrunk by more than `catalog_shrink_guard`). |
| `GET /api/catalog/series/{id}` | `{"status", "languages", "series": {"id", "title", "key", "auto_request", "series_id", "gone"}, "items": [{"id", "kind", "bazarr_id", "title", "season", "episode", "unit_id", "source", "has_source", "gone", "targets": [<target>]}]}`. `unit_id` is set once the worker keeps a unit for the item (it opens the Episode page). |
| `GET /api/catalog/movies` | The same `items` for movies. |
| `POST /api/catalog/sync` | **Sync now**: reads Bazarr, and if the answer was accepted lets the `auto_request` policy act. Returns the status plus `"auto_requested": {"created", "capped", "considered"}` (null when the read was set aside). A read never creates a job by itself. |
| `PUT /api/catalog/series/{id}/policy` | `{"auto_request": "" \| "on" \| "off"}`: follow the global `auto_request` setting, force it on, or off for this series. |
| `POST /api/catalog/ignore` · `POST /api/catalog/unignore` | Select as for `POST /api/jobs` (`items`, `series`/`season`, or `all`), optional `languages`. Records or removes the decision "do not translate this language of this item": neither a sync nor the policy creates a job for it. Returns `{"ok", "changed", "ignored"}`. |
| `POST /api/jobs` | Request translations (below). |

### POST /api/jobs

Name what the request is about in **exactly one way**: `{"items": [catalog ids]}` (a series id expands to its
episodes), `{"series": id}` (optionally with `"season": 1`), or `{"all": true}`. Optional: `"languages": ["vi"]`
(default: every enabled target language), `"mode": "fill"` (default: only a language that is missing; never
overwrites) or `"replace"` (retranslate: the current subtitle becomes a version first), `"estimate_only": true`.

With `estimate_only` nothing is made. The answer (and the real answer too) is
`{"estimate_only", "mode", "estimate": {"jobs", "cues", "calls", "calls_left_today", "paused_until", "minutes", "finish_at"},
"planned_total", "planned": [...first 200 rows], "skipped_total", "skipped_by_reason": {"already requested": 2, ...},
"skipped": [...first 200 rows]}`; a real request adds `"created": [job ids]`, `"count"` and `"merged"` (a replace
request that turned an open `fill` job into the replacement). A row is `{"item_id", "title", "lang", "cues", "calls",
"skip", "reason"}`. `calls` is the planner's count of model calls to translate; story-memory extraction, repairs and
splits add some. `calls_left_today` is what `max_calls_per_day` still allows (null: no cap); `minutes` and `finish_at`
use the average of recent calls (null until a call was timed) and a spent quota (`paused_until`).

A request for something that already has an open job makes no second job (it lifts the first to the interactive
class). The reasons an item is left out: `ignored by the admin`, `the source and the target language are the same`,
`no source subtitle in a configured source language`, `the source subtitle is not on disk`, `the source subtitle
cannot be read`, `the item already has a subtitle in this language`, `already requested`, `outside GST_ONLY_PATHS`.
Jobs made here have `origin: manual` and the interactive priority class, so they also bring back a subtitle
the admin removed; the policy's have `origin: policy` and the automatic class.

## Library, episodes, corrections, versions and glossaries

These endpoints back the Library, Episode, Glossary, Style and Compare pages. Errors are
`{"error": "..."}` with `400` (bad request, invalid glossary or style) or `404` (unknown
unit, series, version or language). A *unit* is one episode or one movie; ids
come from `GET /api/library/...`. Cues are addressed by their 1-based number `n`
in the source subtitle (the same number the model sees), not the SRT index.
`lang` is a target language code and may be omitted when the unit has exactly
one target.

Statuses: `current`, `stale` (the rules or the story behind earlier episodes
changed since translation; never retranslated automatically), `incomplete`
(translated while an earlier episode had no subtitle or facts; the target has
`missing_units` such as `["S01E03"]`), `untranslated`.

### GET /api/library/series
`{"items": [{"id", "key", "title", "stale_policy", "units", "counts": {"current", "stale", "incomplete", "untranslated"}}]}`

### GET /api/library/series/{id}/units?status=
`{"series": {...}, "languages": ["vi"], "units": [{"id", "season", "episode", "title", "status", "targets": [{"lang", "status", "version", "trigger", "model", "missing_units", "updated_at", "exists", "modified_outside"}]}]}`.
`modified_outside` is true when the file on disk is not the version the worker wrote.

### GET /api/library/movies?status=
`{"items": [<unit>...]}` in the same shape as the units above.

### GET /api/units/{id}
One unit with its targets, its series, whether the source subtitle is still on disk
and `memory_applicable` (an episode of a series, so the Context tab applies).

### GET /api/units/{id}/cues?lang=&at=&q=&offset=&limit=
Source and translation side by side. `at=00:23:10` (also `23:10`, `1:02:03.5`)
sets `focus` to the cue playing then, or the nearest one, and returns the page
containing it. `q` keeps cues whose source or translation contains the text.
Default `limit` and maximum is 5000.

```json
{"lang": "vi", "total": 531, "offset": 0, "focus": 112, "aligned": true, "has_target": true,
 "rows": [{"n": 112, "index": 112, "start": "00:23:10", "start_ms": 1390000, "end_ms": 1392500,
           "source": "...", "target": "...", "locked": false}]}
```

### GET /api/units/{id}/corrections?lang=
Active episode-scope corrections: `{"items": [{"id", "kind", "payload", "created_at"}]}`.

### POST /api/units/{id}/corrections
```json
{"lang": "vi", "kind": "term", "scope": "episode",
 "payload": {"source": "Kyber crystal", "target": "tinh thể Kyber", "locked": true}}
```
`kind` and `payload`:

| kind | payload | model call |
| --- | --- | --- |
| `term` | `source`, `target`, optional `locked` (default true), `aliases`, `note` | on the next retranslation |
| `address` | `a`, `b`, `a_to_b` and/or `b_to_a` (`"self–other"`), optional `from_cue`, `note` | on the next retranslation |
| `note` | `text` (episode scope only) | on the next retranslation |
| `cue_edit` | `cue`, `text`; or `edits: [{cue, text}]` (episode scope only) | none: a new version is written at once, Bazarr is asked to rescan, and the cues are locked |

`scope` is `episode` (stored in `app.db`), `forward` (from this episode on) or
`series`; the last two write the series' glossary (the movie's own for movies)
and return `impact: {"stale_now", "stale_after", "would_stale"}`. A `cue_edit`
reply has `version` and `warnings` (for example tags that differ from the
source). Locked cues are kept verbatim by every later retranslation and shown to
the model as reference translations.

### POST /api/units/{id}/corrections/{correction_id}/remove
Deactivates an episode correction (a cue edit stops locking its cue; the file is unchanged).

### POST /api/units/{id}/retranslate
```json
{"lang": "vi", "range": {"cues": [120, 180]}, "correction_ids": [3, 4],
 "new_corrections": [{"kind": "note", "payload": {"text": "Bob is sarcastic"}}]}
```
`range` is `{"cues": [first, last]}`, `{"time": ["00:23:10", "00:25:00"]}` or
omitted for the whole episode. A range needs an existing target with the same
number of cues; cues outside it stay exactly as they are. `correction_ids`
limits which active term/address/note corrections are used (default: all).
Queues a job ahead of automatic work (priority 100); only one retranslation per
target can be queued at a time. Reply: `{"ok": true, "job_id", "range", "correction_ids"}`.
The result overwrites the target and is recorded as a version; the old file is
kept in `backups/subtitles/`.

### GET /api/units/{id}/versions?lang=
`{"lang", "modified_outside", "versions": [{"version", "trigger", "origin", "status", "parent_version", "model", "sha256", "tokens_in", "tokens_out", "created_at", "current", "params"}]}`, newest first.
`trigger` is `auto`, `retranslate`, `variant`, `cue_edit`, `revert`, `external` (a hand edit kept before an overwrite) or `remove`.
`params` says how a version was made when its other fields do not: `{"range": {"first", "last"}}` for a retranslated range
(the version is still stamped with the rules and context of the whole episode, so only the other episodes stay stale,
FR-11.3.6) and `{"variant": {"label", "preset", "style"}}` for a variant; `{}` for any other.
`origin` is `machine`, `edit`, `revert` or `external`. `status` is `current`, `superseded`, `candidate` (a result that is
not on disk: the file appeared while the job ran, the subtitle changed under a replace job, or the source changed; adopt it to
publish it), `removed` (the admin deleted the subtitle; restoring it is a revert) or `pruned` (its stored copy was
removed by file cleanup, FR-13.3: the line stays, there is no copy to restore or to diff, and reverting to it answers
`400` with the reason).

### GET /api/units/{id}/versions/{v}/diff?against=&lang=
Cue-level changes from `against` (default: the previous version) to `v`:
`{"version", "against", "changed_cues", "changes": [{"op", "before": [{n, index, time, text}], "after": [...]}]}`.

### POST /api/units/{id}/versions/{v}/revert
Body `{"lang": "vi"}`. Restores version `v` byte for byte as a new version (what
was on disk is kept first) and asks Bazarr to rescan. Cue locks whose text is no
longer in the restored file are released. Reply `{"ok": true, "version", "restored"}`.

### POST /api/units/{id}/versions/{v}/adopt
Body `{"lang": "vi"}`. "Dùng bản này": a `candidate` version is written to the file and becomes `current`; the
version that was on disk becomes `superseded`. Only a candidate can be adopted (`400` otherwise). Reply
`{"ok": true, "version", "adopted"}`.

### POST /api/units/{id}/output/remove
Body `{"lang": "vi"}`. Deletes the target subtitle file. The last version is kept as a `removed` version, so
the subtitle can be restored by a revert. The episode shows as removed and an automatic job does not request
it again; only a manual request does. Needs a current version (`404` otherwise). Reply `{"ok": true, "version"}`.

### GET /api/units/{id}/context?lang=
The story memory behind a translation (episodes of a series only; a movie gets
`{"applicable": false, "enabled": false}`). Reads stored facts only and never calls a model.

```json
{"applicable": true, "enabled": true, "policy": "", "specials_after": "", "label": "S01E06",
 "special": false, "lang": "vi",
 "used": {"version": 2, "complete": false, "missing": ["S01E03"], "changed": true,
          "sections": [{"title": "Story so far", "body": "..."}]},
 "now": {"fingerprint": "...", "approximate": false,
         "sections": [{"title": "Relationship state", "body": "..."}],
         "prior": [{"label": "S01E01", "has_source": true, "has_facts": true}],
         "missing": ["S01E03"], "own_facts": {"summary": "...", "...": "..."}, "own_has_source": true}}
```
`used` is the context of the current version of the target (`null` when it was made
without memory); `changed` means the facts it was built from differ from the ones
stored now, which is why the translation is `stale`. `now` is what a translation
started at this moment would get; `approximate` is true when old summaries could
only be shortened, not condensed by the model (that needs a call). `policy` is the
series switch (`""` follows the global `memory_enabled`, `"on"`, `"off"`).

### POST /api/units/{id}/context/extract
Reads the facts of this episode and of every earlier episode that has a source
subtitle and no facts yet (**spends model calls**, one per episode, using
`memory_model`). Reply `{"ok": true, "extracted": ["S01E01"], "results": {"S01E01": "extracted"}}`;
a result is `cached`, `extracted`, `stored`, `no-source` or `failed`. A provider failure
(quota, outage, missing key) is a `400` naming the error type.

### GET /api/series/{id}/memory
`{"series", "policy", "specials_after", "global_enabled", "units": [{"id", "label", "season", "episode", "has_source", "has_facts", "facts_hash"}]}`,
in story order.

### PUT /api/series/{id}/memory
Body `{"policy": "" | "on" | "off", "specials_after": "S02E05"}`, both optional.
`policy` switches story memory on or off for this series against the global setting.
`specials_after` is where season 0 (specials) sits in the story: its context is the
state after that episode (blank: after the last episode of the first season); its own
facts never enter the main storyline. Changing either can make translations `stale`;
nothing is retranslated. Reply: the same as `GET`.

### GET /api/series/{id}/glossary?lang=
`{"lang", "exists", "doc", "yaml", "version", "stale_units"}`. `lang` is required. The glossary lives in the database
(P12); `doc` is the YAML schema as a JSON object (what the form edits), `yaml` is its export, `version` counts saves
(0 when there is none).

### PUT /api/series/{id}/glossary?lang=
Body `{"doc": {...}, "dry_run": false}` (the form) or `{"yaml": "...", "dry_run": false}` (an import). The document is
validated (`400` names the offending path, for example `terms[0]: missing required field 'target'`) and stored; what it
replaces goes to the history. Reply `{"ok", "saved", "version", "stale_now", "stale_after", "would_stale"}`; with
`dry_run` nothing is stored. Saving the same glossary again changes nothing.

### GET /api/series/{id}/glossary/history?lang=
What each save replaced, newest first: `{"lang", "items": [{"version", "at", "note", "terms", "characters", "address_rules"}]}`.
`note` says what made the replacing save: `console`, `import`, `correction`, `restore` or `copy`. The count kept per
series and language is `retention_glossary_keep`.

### POST /api/series/{id}/glossary/restore
Body `{"lang": "vi", "version": 3}`. Puts an earlier state back as a new save (the one it replaces goes to the history, so
a restore can be undone). `404` for a version that is not in the history.

### POST /api/series/{id}/glossary/copy
Body `{"lang": "vi", "from_series": 12, "merge": true}`. Copies the other series' glossary into this one: a copy, not a
link. With `merge` this series keeps its own entries on conflicts; without it the glossary is replaced. Reply
`{"ok", "version", "from"}`; `404` when the other series has no glossary in that language.

## Style, character cards and presets

A style (P12, FR-18) is a set of axes and subtitle limits; every field is optional. Layers, lowest first: the target
language, the series' preset, the series, the episode, and a character's card. The glossary always wins. A style is
`{"formality": "casual|neutral|formal", "tone": "text", "profanity": "keep|soften|remove",
"localization": "literal|balanced|adapt", "dialect": "north|south|neutral", "honorifics": "keep|translate",
"max_cps": 17, "max_line_length": 42, "max_lines": 2, "allow_condense": true, "notes": "text"}`; an unknown or
invalid field is a `400` naming it. Changing a style, a preset a series uses, or a card flags the translations made
without it as `stale`; nothing is retranslated. Every save route takes `dry_run` and then only counts
`{"stale_now", "stale_after", "would_stale"}`.

### GET /api/series/{id}/style?lang=
`{"series", "lang", "preset", "style", "effective", "presets": [...], "cards": [...]}`. `effective` is what is in force
once the language and the preset are laid under the series' own style. No network call is made.

### PUT /api/series/{id}/style?lang=
Body `{"preset": "sitcom" | "", "style": {...}, "dry_run": false}`. Reply `{"ok", "saved", "stale_now", "stale_after", "would_stale"}`.
`404` for a preset that does not exist.

### GET /api/series/{id}/style/suggestions
Asks TMDB (through the metadata port) for the series' genres and returns `{"genres": [...], "presets": [...]}`, best fit
first (an anime before a drama). Empty without a TMDB key or a match; never an error.

### POST /api/series/{id}/style/preview
"Thử style" (FR-18.7). Body `{"lang": "vi", "unit_id": 34, "count": 20, "cues": [12, 40], "preset": "", "style": {...}}`, all
optional: the episode defaults to the first one with a translation, the cues to about twenty spread over it (or the ones
named), the style to the stored one (or the draft given, saved or not). Translates those lines with the default model
and returns `{"rows": [{"n", "source", "current", "preview"}], "model", "tokens_in", "tokens_out", "effective",
"voices", "bundle_hash", "style_hash"}`. **Spends model calls; writes nothing**: no file, no version. A provider failure is
a `400` naming the error type.

### PUT /api/series/{id}/cards?lang=
Body `{"card": {"name": "Bob", "aliases": [], "speech": "fast, sarcastic", "address": [{"to": "Alice", "form": "anh–em"}],
"catchphrases": [], "samples": [], "style": {"formality": "casual"}, "always": false}, "replaces": "old name", "dry_run": false}`.
Creates or replaces a card (`replaces` renames). A card is in a prompt only for an episode whose dialogue names the
character (or all, with `always`). Reply `{"ok", "saved", "card", "stale_now", "stale_after", "would_stale"}`.

### POST /api/series/{id}/cards/delete?lang=
Body `{"name": "Bob"}`. Reply `{"ok", "stale_now", "stale_after", "would_stale"}`; `404` for an unknown card.

### A movie's glossary, style and cards: `/api/movies/{unit_id}/...`
A movie has the same pages as a series (P12b), reached by the **movie's unit id** (`id` of an item in `GET /api/catalog/movies`
or `GET /api/library/movies`). They use the rows of owner `movies/<movie key>`, so a movie's glossary and style are its
own and never reach a series. The stale count (`stale_now`, `stale_after`, `would_stale`, `stale_units`) is worked out
for the movie's one unit. A unit id that is missing or belongs to an episode is a `404`.

| Method and path | Same as |
|---|---|
| `GET /api/movies/{unit_id}/glossary?lang=` · `PUT …` | `GET`/`PUT /api/series/{id}/glossary` |
| `GET /api/movies/{unit_id}/glossary/history?lang=` · `POST …/glossary/restore` | the series' history and restore |
| `GET /api/movies/{unit_id}/style?lang=` · `PUT …` | the series' style; the reply names the movie under `"movie": {"id", "title"}` instead of `"series"` |
| `POST /api/movies/{unit_id}/style/preview` | "Thử style" on the movie's own source subtitle (`unit_id` in the body, if given, must be the movie's); spends model calls, writes nothing |
| `PUT /api/movies/{unit_id}/cards?lang=` · `POST …/cards/delete` | the series' cards |

There is no `…/glossary/copy` (a glossary is copied between series) and no `…/style/suggestions`. The style of the movie's
unit itself (the "episode" override) stays at `GET`/`PUT /api/units/{id}/style`.

### GET /api/style/presets
`{"items": [{"id", "name", "genres", "style", "builtin"}]}`. The five built-in presets (sitcom, drama, anime, documentary,
kids) are stored once and belong to the admin from then on.

### PUT /api/style/presets/{id}
Body `{"name", "genres": ["Comedy"], "style": {...}}`. Edits a preset (`404` for an unknown one). Series that use it are
flagged stale.

### POST /api/style/presets/{id}/clone
Body `{"name": "My sitcom"}`. A copy under a new id (`my-sitcom`) to edit; `400` when the id is taken.

### POST /api/style/presets/{id}/delete
Deletes a preset the admin made (`400` for a built-in one: edit it instead).

### GET /api/units/{id}/style · PUT /api/units/{id}/style
`{"style": {...}}`: the episode's own override, the layer above the series'. An empty style removes it.

### POST /api/units/{id}/variant
Body `{"lang": "vi", "label": "warmer", "preset": "", "style": {...}}`. Queues a **variant** job (kind `variant`,
origin `variant`): the whole episode translated with that style in place of the series' own and the episode's
override, kept as a `candidate` version whose `params.variant` says `{label, preset, style}`. It never goes on disk
by itself and ignores an existing target. At most one variant per episode and language is open at a time (`400`).
Reply `{"ok", "job_id", "label"}`.

### GET /api/units/{id}/versions/{v}/compare?lang=
The current translation and a version side by side: `{"lang", "version", "status", "params", "model", "aligned",
"differing", "rows": [{"n", "source", "current", "variant", "differs"}]}`.

### POST /api/units/{id}/versions/{v}/choose
Body `{"lang": "vi", "picks": {"12": "variant", "13": "current"}, "whole": "variant", "apply": false}`. Records the admin's
verdict on a candidate: one `ab` quality signal per pick (winner `variant`, `current` or `tie`) and one for `whole`, each
carrying the variant's label, style, model and prompt version, so scores add up by style and prompt version. With
`apply` the verdict is acted on: the cues won by `variant` become cue edits of the current translation, and a
`whole` win adopts the candidate. `400` when the version is not a candidate. Reply `{"ok", "recorded", "applied"}`.
