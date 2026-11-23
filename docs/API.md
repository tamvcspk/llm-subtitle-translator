# HTTP API

The web console uses these endpoints. They are intentionally small and local to
the worker; do not expose them directly to the internet without a trusted
reverse proxy that adds TLS.

## The shape of an answer

Every route that answers JSON has a model of its answer (`apps/server/subserver/api/answers`), and the models are exported
as an OpenAPI document, [`openapi.json`](openapi.json), which is committed. That document is the one place that says which
fields an answer has, which can be left out (`maybe()` in the model, `?:` in TypeScript) and which can be `null`;
`apps/web` generates its types from it (`npm run gen:api` makes `apps/web/src/api/schema.gen.ts`), and CI fails when the
document is not what the code says or the types are not what the document says. The pages below say what a route is for
and what its fields **mean**; where they list fields, the list is for orientation, and the document wins.

Export the document with `uv run --locked --directory apps/server python -m subserver.api.openapi` (no server needed).

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

## Errors

Every error answer of `/api/*` is JSON, `{"error": "<message>"}`, with the status that says what went wrong: `400` for
input the worker refuses (a field out of range, a body that is not a JSON object, a glossary that does not read), `404`
for an id nobody has and for a `/api/...` path that is not a route (it is never the console's page), `405` for a route
called with a method it does not have, `409` for a state that forbids the call, and `500` (also written to the worker
log) for anything unexpected. The server is a FastAPI app (uvicorn, one process, port `6789`); it serves no `/docs`,
`/redoc` or `/openapi.json`, and a route that is not in this document does not exist.

A request body is a JSON object, read as JSON whatever its `Content-Type` (the one file upload, `POST /api/backups/import`, is the exception: its body is the zip). A body that is not an object, or a field of the wrong type, is a `400` that names the field: `{"error": "estimate_only: Input should be a valid boolean"}`. Fields the worker does not know are ignored. A flag that makes a call spend (`estimate_only`, and `dry_run` of the bulk calls) must be a real boolean: `"true"` or `1` is refused, not read as no. The two flags whose safe side is the default keep their rule: `dry_run` of `POST /api/cleanup` is `false` only when it is exactly `false`, and `include_keys` of `POST /api/backups` is `true` only when it is exactly `true`.

### POST /api/auth/login

`{"user": "...", "password": "..."}` → `200 {"ok": true, "user": "..."}` and a
`Set-Cookie: gst_session=...; HttpOnly; SameSite=Strict; Max-Age=604800` (plus
`Secure` when the request carries `X-Forwarded-Proto: https`). Wrong
credentials: `401`, no cookie. A session expires after 7 days without use. With `CONSOLE_COOKIE_SAMESITE=none` (a console in
an iframe of another site) the cookie is `SameSite=None; Secure` behind HTTPS and `SameSite=Lax` over plain HTTP.

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

Returns queue counts, current settings (as `GET /api/settings`), the Bazarr URL and Bazarr's answer to a ping (`{"error": "..."}` when it cannot be asked), `providers` and `model_layers`.

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
(running), `deferred` (waiting, see `wait_reason`), `done` (includes `skipped`), `failed` and `cancelled`
(a job the admin cancelled before it started or stopped while it ran: one state for both);
each list is the latest 100 jobs of its state, `counts` has the real numbers.

Each waiting job carries `wait_reason` and `retry_at` (Unix seconds, when it may run
again). The reasons are `quota` (daily quota spent), `provider` (key refused, model
unknown, or breaker open), `context` (earlier episodes' facts missing; `missing_units`
names them), `settle` (source subtitle changed a moment ago) and `rate` (provider busy,
retrying soon). A wait is never a failure. A running job carries `stage`, and
`progress_done` / `progress_total` while it translates. `attempts` counts restarts after
a crash; a job that crashed `max_crashes` times is `failed` with `error` starting
`crashed:`.

Each job also has `title` (the series, or the movie's title) and `label` (`S01E04`; empty for a movie), read from its unit
when the list is built (both empty for a job made before units existed or whose unit is gone; fall back to the file
name in `subtitle_path`).

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

## POST /api/queue/cancel

`{"job_id": "..."}`. Removes a **failed** job for good and returns `{"ok": true}` (`false` when the job is not in `failed`).
An older route, kept for scripts: the console uses `POST /api/queue/delete` (same effect for a failed job) and
`POST /api/queue/stop` (the way to end a job that has not finished).

## POST /api/queue/request-prerequisites

`{"job_id": "..."}` for a job with `wait_reason` `context`. Queues the earlier episodes the
job waits for, when their source subtitle is on disk, at interactive priority. Returns
`{"requested": ["<job id>"], "unavailable": ["S01E02"]}`; `unavailable` names the earlier
episodes that have no source subtitle yet. Asking again makes no second job. Returns
`400` for a job that is not waiting for earlier episodes.

**Estimate first (P12b, UX 6.4).** With `"estimate_only": true` nothing is queued: the reply is `{"estimate_only": true,
"estimate": {"jobs", "cues", "calls", "calls_left_today", "paused_until", "minutes", "finish_at"}, "planned_total",
"planned": [{"title": "S01E01", "lang", "cues", "calls"}], "skipped_total", "skipped_by_reason", "unavailable": ["S01E02"]}`;
an episode with no source subtitle that can be read is under `unavailable`.

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

Returns Bazarr-supported language choices normalized for the web console: `{"items": [{"code", "code3", "name", "language", "enabled_in_bazarr"}]}`. A fallback list is returned when Bazarr cannot be asked.

## GET /api/gemini-models

Returns the model choices for the Settings dropdown (the name is from before the second provider). With
`llm_provider` `gemini`: if the Gemini API cannot be reached or no key is configured, the worker returns a bundled
fallback list. With `openai_compat`: the models the server lists at `{llm_base_url}/models` (asked with the stored
key, if any), or an empty list when the address is unset or the server cannot be reached. The answer is `{"items": [{"id", "name"}]}`.

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
  "judge_enabled": false,
  "judge_model": "",
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
  "media_roots": ["/media"]
}
```

The wanted list, the media scan and `scan_limit` (`GET /api/wanted`, `GET /api/scan`, `POST /api/enqueue-scan`) are gone
(P12b): the Library (`/api/catalog`) replaces them. A `scan_limit` in an old backup or request is ignored.

`POST /api/enqueue` is gone too (review W4, R10-8): it made a job straight from a body, skipping Ignored items, the
estimate, `GST_ONLY_PATHS` and the unit id that stops a duplicate. Ask for translations with `POST /api/jobs` (Library).

## POST /api/series/{id}/memory/adopt

The Context tab of a series calls it once when it opens: the episodes the catalog lists for that series get the units
the tab shows (idempotent). `{"ok": true, "adopted": <units made>}`. `GET /api/series/{id}/memory`, which the page polls,
never writes.

## POST /api/queue/retry

Moves a failed job back to pending with a fresh retry cycle (waits, breaker and call counts
are reset). Returns `{"ok": false}` when the job is not failed or the same file already has an
open job.

```json
{"job_id": "7fefefb6fdb4f200bbb5fcac70e917492cea4d8f"}
```

**Estimate first (P12b, UX 6.4).** With `"estimate_only": true` nothing is retried; the answer says what retrying would cost.
Name the jobs with exactly one of `job_id`, `job_ids` (a list) or `all` (every failed job: Retry All Failed), else `400`.
Reply, in the shape of a bulk request's estimate: `{"estimate_only": true, "estimate": {"jobs", "cues", "calls",
"calls_left_today", "paused_until", "minutes", "finish_at"}, "planned_total", "planned": [{"job_id", "title", "lang",
"cues", "calls"}], "skipped_total", "skipped_by_reason", "skipped": [{..., "reason"}]}` (at most 200 rows of each are
named). A job that is not failed (`the job is not failed`), is unknown (`no such job`), whose file already has an open
job (`already requested`) or whose source cannot be read is under `skipped`, as the real retry would do nothing for it.
A retranslation of a range that failed costs that range only.

**Retry several (P12b).** Without `estimate_only`, `job_ids` (a list) or `all` (every failed job: Retry All Failed) retries
each of them the same way and answers `{"ok", "count", "skipped"}`: `count` jobs were put back in line, `skipped` were
failed no more or had an open job for the same file and were left alone (`ok` is false only when asked for some and none
could go back). Naming more than one way (`job_id` with `all`, say) is a `400`.

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

An event is `{"id", "at", "level", "kind", "job_id", "unit_id", "reason", "data"}`; the answer is `{"items", "next_before", "telemetry": {"written", "dropped", "queued"}}`.

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

The reply is `{"dry_run", "at", "groups": [{"name", "enabled", "window", "files", "rows", "bytes", "error"}], "total": {"files", "rows", "bytes"}, "media_temp"}` with the groups `jobs_done`, `jobs_failed`, `versions`, `glossary` and `temp`.

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

Returns `{"ok", "kind", "message"}`; a failure carries `"ok": false` and `"auth_failed": true` when the service refused the key.

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
`{"lang", "content", "removed", "stale", "job", "wait", "ignored", "reason", "text", "job_info"}`: `job_info` is `null` when no job
says anything, else `{"job_id", "progress_done", "progress_total", "retry_at", "error", "missing_units"}` of the open job
(or of the failed one that gives the `failed` label): what a row needs to Cancel, Stop or Retry it and to show its
progress, the time it resumes (`retry_at`, Unix seconds, waiting only), the reason it failed (the last 300 characters) and,
for `wait: "context"`, the earlier episodes it waits for (`["S01E03"]`); fields the job does not have are `null`, `""` or
`[]`. `content` is `missing`,
`translated`, `edited`, `external` (a subtitle the worker did not make) or `outside` (the language is not in
Bazarr's profile for the item; it can still be requested); `job` is `requested`, `running`, `waiting` (with
`wait`: `quota`, `provider`, `context`, `settle`, `rate`), `failed` or `""`; `reason` is `"bootstrap"` on a Missing item
the `auto_request` policy would ask for but holds back until its series' glossary is reviewed (FR-15.12; a reason of the
Library, never a state of a job, and an admin's own request is not held); `text` is the label as the console
shows it, such as `Missing · Requested`, `Translated · Stale`, `Missing · Removed · Ignored` or `Missing · Waiting for
glossary review`.

| Method and path | Answer |
|---|---|
| `GET /api/catalog` | The series (with counts per target language: `missing`, `failed`, `ignored`, and `open_jobs` by state) and the movies' counts. Counts come from the catalog, the jobs and the decisions in a few queries (no per-item work). `failed` and `ignored` are per target language and equal what the `Failed` and `Ignored` labels of the series page (or the movies page) add up to: `failed` counts units with no open job whose newest finished job for that language is `failed` (so a failure asked for again, or followed by a success, is not counted); `ignored` counts the "do not translate" decisions. They feed the Failed and Ignored chips of Home. A series (or movie) Bazarr stopped listing stays in the list with `gone: true` while the worker keeps a unit of it (its translations and history are still there): nothing is missing and nothing can be requested for it; `episodes` counts what its page lists, listed or kept. One the worker never kept anything of is not listed. |
| `GET /api/catalog/status` | The age and state of the cache. `synced_at` is the last accepted read; `status: "kept"` means the last read was set aside and `warning` says why (Bazarr unreachable, empty, or shrunk by more than `catalog_shrink_guard`). `shrink` is `{"listed", "dropped"}` when the guard was the reason (otherwise null; kept in memory, so it is empty again after a restart until the next read): the console offers to accept that read. |
| `GET /api/catalog/series/{id}` | One series: its `bootstrap` state and every episode with its targets. `unit_id` is set once the worker keeps a unit for the item (it opens the Episode page). |
| `GET /api/catalog/movies` | The same `items` for movies. |
| `POST /api/catalog/sync` | **Sync now**: asks the background loop to read Bazarr at once (within a few seconds) and answers immediately with the status plus `"requested": true`; the read, and the `auto_request` policy after an accepted one, run in that loop, never in the request. Poll `GET /api/catalog/status` for the result. Body `{"accept_shrink": true}` lets that one read past the shrink guard when the status said `shrink` (the admin confirmed how many items go). A read never creates a job by itself. |
| `PUT /api/catalog/series/{id}/policy` | `{"auto_request": "" \| "on" \| "off"}`: follow the global `auto_request` setting, force it on, or off for this series. |
| `PUT /api/catalog/series/{id}/bootstrap` | **Continue** (FR-15.12): `{"released": true}` (the default) lets `auto_request` go past the first `auto_request_bootstrap` episodes of a series whose glossary nobody has reviewed; `false` holds it again. Nothing is requested at once: the rest is asked for after the next sync. `series.bootstrap` of the series page says where it stands: `locked` (the policy is on for it and holding episodes back), `limit`, `pending` (suggestions waiting for review), `released`, `held` (labels with `reason: "bootstrap"`). |
| `POST /api/catalog/ignore` · `POST /api/catalog/unignore` | Select as for `POST /api/jobs` (`items`, `series`/`season`, or `all`), optional `languages`. Records or removes the decision "do not translate this language of this item": neither a sync nor the policy creates a job for it. Returns `{"ok", "changed", "ignored"}`. |
| `POST /api/jobs` | Request translations (below). |

### POST /api/jobs

Name what the request is about in **exactly one way**: `{"items": [catalog ids]}` (a series id expands to its
episodes), `{"series": id}` (optionally with `"season": 1`), or `{"all": true}`. Optional: `"languages": ["vi"]`
(default: every enabled target language), `"mode": "fill"` (default: only a language that is missing; never
overwrites) or `"replace"` (retranslate: the current subtitle becomes a version first), `"estimate_only": true`.

With `estimate_only` nothing is made. The answer (and the real answer too) is
`{"estimate_only", "mode", "estimate": {"jobs", "cues", "calls", "calls_left_today", "paused_until", "minutes", "finish_at",
"extract_calls", "extract_model"}, "planned_total", "planned": [...first 200 rows], "skipped_total", "skipped_by_reason": {"already requested": 2, ...},
"skipped": [...first 200 rows]}`; a real request adds `"created": [job ids]`, `"count"` and `"merged"` (a replace
request that turned an open `fill` job into the replacement). A row is `{"item_id", "title", "lang", "cues", "calls",
"skip", "reason"}`. `calls` is the planner's count of model calls to translate; repairs and splits add some.
`extract_calls` (P15g) is the story-fact reads the jobs run first when memory is on for the series: each episode before a
planned one in story order, and the planned one, that has a source subtitle and no facts for its current `sha256` and
extraction prompt version, each counted once for the whole request, on `extract_model` (the `memory_model`; `""` when there
are none); 0 when memory is off or the facts are there. `calls_left_today` is what `max_calls_per_day` still allows (null:
no cap); `minutes` and `finish_at` use the average of recent calls over `calls + extract_calls` (null until a call was
timed) and a spent quota (`paused_until`).

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
The series the worker keeps, each with its `units` and how many are `current`, `stale`, `incomplete` and `untranslated`.

### GET /api/library/series/{id}/units?status=
The series, its target `languages` and its units, each with a `status` (the worst of its targets) and its `targets`. `modified_outside` is true when the file on disk is not the version the worker wrote.

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

The answer has `lang`, `total`, `offset`, `focus`, `aligned` (the target has as many cues as the source), `has_target` and `rows`; a row is `{"n", "index", "start", "start_ms", "end_ms", "source", "target", "locked"}`.

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

With `"estimate_only": true` (P12b) nothing is queued and no correction is made: the same checks run (a bad range, an
unknown correction, a retranslation already queued or a job running are the same `400`) and the reply is `{"ok",
"estimate_only": true, "range", "correction_ids", "estimate": {"jobs", "cues", "calls", "calls_left_today", "paused_until",
"minutes", "finish_at"}}`. `cues` is the size of the range (or of the episode) and `calls` the planner's parts for those cues.
The whole-episode retranslation is also estimated by `POST /api/jobs` with `mode: "replace"`.

### GET /api/units/{id}/versions?lang=
Newest first.
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
The answer lists each `change` (`op`, the cues `before` and `after`) and `changed_cues`.

### POST /api/units/{id}/versions/{v}/revert
Body `{"lang": "vi"}`. Restores version `v` byte for byte as a new version (what
was on disk is kept first) and asks Bazarr to rescan. Cue locks whose text is no
longer in the restored file are released. Reply `{"ok": true, "version", "restored"}`.

### POST /api/units/{id}/versions/{v}/adopt
Body `{"lang": "vi"}`. "Dùng bản này": a `candidate` version is written to the file and becomes `current`; the
version that was on disk becomes `superseded`. Only a candidate can be adopted (`400` otherwise). Reply
`{"ok": true, "version", "adopted"}`.

Optional (P12b), both only as JSON `true`: `"apply_style": true` also keeps the variant's preset and style as the series'
(or the movie's) own, in one request, for a version made by a variant (`400` when it was not, or when its preset no longer
exists; nothing is adopted then). The reply adds `"style": {"preset", "style"}` and `{"stale_now", "stale_after",
"would_stale"}` for the **other** episodes (this one is replaced by the variant, so it is not counted); they are flagged stale,
never retranslated. `"dry_run": true` (needs `apply_style`) only returns that count and `"adopted": null`: nothing is adopted or saved.

### POST /api/units/{id}/output/remove
Body `{"lang": "vi"}`. Deletes the target subtitle file. The last version is kept as a `removed` version, so
the subtitle can be restored by a revert. The episode shows as removed and an automatic job does not request
it again; only a manual request does. Needs a current version (`404` otherwise). Reply `{"ok": true, "version"}`.

### GET /api/units/{id}/context?lang=
The story memory behind a translation (episodes of a series only; a movie gets
`{"applicable": false, "enabled": false}`). Reads stored facts only and never calls a model.

The answer says whether memory `applicable`/`enabled` is for the unit, its series `policy`, the `label` and whether it is `special` (season 0), the context `used` by the current translation and the context `now`.

`used` is the context of the current version of the target (`null` when it was made
without memory); `changed` means the facts it was built from differ from the ones
stored now, which is why the translation is `stale`. `now` is what a translation
started at this moment would get; `approximate` is true when old summaries could
only be shortened, not condensed by the model (that needs a call). `policy` is the
series switch (`""` follows the global `memory_enabled`, `"on"`, `"off"`).

### POST /api/units/{id}/context/extract
Reads facts (**spends model calls**, one per episode, using `memory_model`), as jobs: it makes one `extract` job per
episode that needs reading, in story order, and answers at once; the queue runs them like any other job (daily quota,
breaker and `max_calls_per_day` apply, **Stop** works between episodes, a restart loses nothing). Body, optional:
`{"unit_ids": [502, 504]}`.

- Without `unit_ids` it reads this episode and every earlier episode of the story. With `unit_ids` it reads exactly those
  episodes (all of this series, a whole-number list that is not empty; `400` otherwise), in story order, and nothing
  before them.
- Reply `{"ok": true, "job_ids": ["extract:502-…"], "queued": 1, "merged": 0, "skipped": {"S01E01": "cached"}}`. An
  episode that already has an open job is not made twice (`merged`: that job is lifted to the admin's class). `skipped`
  says why an episode got no job: `no-source` (no source subtitle on disk) or `cached` (the newest stored facts are those
  of its source as it is now). The request spends nothing itself.
- A job's `result` is `extracted`, `cached`, `stored` (the facts it already had were kept) or `skipped-no-source` (the
  subtitle went before the job ran). A failed read (the model's answer was not usable) is a failed job with the reason;
  **Retry** puts it back in line. A provider failure never fails the job: it waits, as a translation does.
- There is no `background` parameter and no synchronous path any more.

### GET /api/units/{id}/facts
What the stored facts of this episode say, for the console to show (reads only, never a model call): its `characters`, who stands how to whom (`relationship_events`), its `terms` and its `summary`.
`stored` is `false` and `facts` is `null` until the facts were read. `400` for a movie, `404` for an unknown unit.

### GET /api/series/{id}/memory
The series, its `policy` and `specials_after`, whether memory is on globally, and its episodes (`units`) in story order with whether each has a source and stored facts.

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

### GET /api/series/{id}/glossary.yaml?lang=
The saved glossary as a file: `Content-Type: application/x-yaml`, `Content-Disposition: attachment; filename="glossary-<title>-<lang>.yaml"`
(the title reduced to `a-z0-9-`), so a plain link downloads it (the console never uses `blob:`). A series without a glossary
in that language gives an empty file. `lang` is required (`400`); `404` for an unknown series. A movie has the same at
`GET /api/movies/{unit_id}/glossary.yaml?lang=`.

### GET /api/series/{id}/glossary/history?lang=
What each save replaced, newest first: `{"lang", "items": [{"version", "at", "note", "terms", "characters", "address_rules"}]}`.
`note` says what made the replacing save: `console`, `import`, `correction`, `restore` or `copy`. The count kept per
series and language is `retention_glossary_keep`.

### POST /api/series/{id}/glossary/restore
Body `{"lang": "vi", "version": 3, "dry_run": false}`. Puts an earlier state back as a new save (the one it replaces goes
to the history, so a restore can be undone). Reply `{"ok", "saved", "version", "restored", "stale_now", "stale_after",
"would_stale"}`; with `dry_run` nothing is restored, `version` is `null` and the counts say what the restore would flag.
`404` for a version that is not in the history.

### POST /api/series/{id}/glossary/copy
Body `{"lang": "vi", "from_series": 12, "merge": true}`. Copies the other series' glossary into this one: a copy, not a
link. With `merge` this series keeps its own entries on conflicts; without it the glossary is replaced. Reply
`{"ok", "saved", "version", "from", "stale_now", "stale_after", "would_stale"}`; with `"dry_run": true` nothing is copied
and `version` is `null`. `404` when the other series has no glossary in that language.

## Style, character cards and presets

A style (P12, FR-18) is a set of axes and subtitle limits; every field is optional. Layers, lowest first: the target
language, the series' preset, the series, the episode, and a character's card. The glossary always wins. A style is
`{"formality": "casual|neutral|formal", "tone": "text", "profanity": "keep|soften|remove",
"localization": "literal|balanced|adapt", "dialect": "north|south|neutral", "honorifics": "keep|translate",
"max_cps": 17, "max_line_length": 42, "max_lines": 2, "allow_condense": true, "notes": "text"}`; an unknown or
invalid field is a `400` naming it. Changing a style, a preset a series uses, or a card flags the translations made
without it as `stale`; nothing is retranslated. Every route that can change the rules (a save, a restore or copy of a
glossary, an edit or delete of a preset, adopting a variant with its style) takes `dry_run` and then only counts
`{"stale_now", "stale_after", "would_stale"}`; the real call answers with the same three. A style, a preset and a card
apply to **every language** of the translations they reach, so their count is over all languages (a `lang` is accepted
and does not narrow it); a glossary is written in one language, so its count is for that `lang`.

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

### PUT /api/series/{id}/characters?lang=
One character, both halves **in one transaction** (P12b): its Name (a glossary document) and its Voice (a card). Body
`{"glossary": {...document...}, "card": {"card": {...}, "replaces": "old name"}}` (each part optional) or, to delete,
`{"glossary": {...document without the Name...}, "delete_card": "Bob"}`. Everything is validated first (`400` names the
offending path, for example `card.speech: expected text`), then written together or not at all (a `delete_card` that names
no card is `404` and writes nothing). Saving a card and deleting one in the same request, or sending nothing, is `400`.
Reply `{"ok", "saved"}`; the live count of what it would flag stale comes from the `dry_run` of the glossary and card
routes, as before. A movie has the same at `PUT /api/movies/{unit_id}/characters`.

### A movie's glossary, style and cards: `/api/movies/{unit_id}/...`
A movie has the same pages as a series (P12b), reached by the **movie's unit id** (`id` of an item in `GET /api/catalog/movies`
or `GET /api/library/movies`). They use the rows of owner `movies/<movie key>`, so a movie's glossary and style are its
own and never reach a series. The stale count (`stale_now`, `stale_after`, `would_stale`, `stale_units`) is worked out
for the movie's one unit. A unit id that is missing or belongs to an episode is a `404`.

| Method and path | Same as |
|---|---|
| `GET /api/movies/{unit_id}/glossary?lang=` · `PUT …` | `GET`/`PUT /api/series/{id}/glossary` |
| `GET /api/movies/{unit_id}/glossary/history?lang=` · `POST /api/movies/{unit_id}/glossary/restore` | the series' history and restore (with `dry_run`) |
| `GET /api/movies/{unit_id}/style?lang=` · `PUT …` | the series' style; the reply names the movie under `"movie": {"id", "title"}` instead of `"series"` |
| `POST /api/movies/{unit_id}/style/preview` | "Thử style" on the movie's own source subtitle (`unit_id` in the body, if given, must be the movie's); spends model calls, writes nothing |
| `PUT /api/movies/{unit_id}/cards?lang=` · `POST /api/movies/{unit_id}/cards/delete` | the series' cards |

There is no `…/glossary/copy` (a glossary is copied between series) and no `…/style/suggestions`. The style of the movie's
unit itself (the "episode" override) stays at `GET`/`PUT /api/units/{id}/style`.

### GET /api/style/presets
`{"items": [{"id", "name", "genres", "style", "builtin"}]}`. The five built-in presets (sitcom, drama, anime, documentary,
kids) are stored once and belong to the admin from then on.

### PUT /api/style/presets/{id}
Body `{"name", "genres": ["Comedy"], "style": {...}, "dry_run": false}`. Edits a preset (`404` for an unknown one). Series
and movies that use it are flagged stale. Reply: the preset (`id`, `name`, `genres`, `style`, `builtin`) plus `{"ok",
"saved", "stale_now", "stale_after", "would_stale", "owners"}` (`owners` is how many series and movies use it); with
`dry_run` only `{"ok", "saved": false, ...counts}` and nothing is stored.

### POST /api/style/presets/{id}/clone
Body `{"name": "My sitcom"}`. A copy under a new id (`my-sitcom`) to edit; `400` when the id is taken.

### POST /api/style/presets/{id}/delete
Deletes a preset the admin made (`400` for a built-in one: edit it instead). A series that named it loses the layer, so
the reply counts what that flags: `{"ok", "saved", "stale_now", "stale_after", "would_stale", "owners"}`. Body
`{"dry_run": true}` only counts and deletes nothing.

### GET /api/units/{id}/style · PUT /api/units/{id}/style
`{"style": {...}}`: the episode's own override, the layer above the series'. An empty style removes it. `PUT` takes
`{"style": {...}, "dry_run"?}` and answers `{"ok", "saved", "style", "stale_now", "stale_after", "would_stale"}`, counted
for this one episode in **every language** it is translated into (a `lang` in the body is ignored); with `"dry_run": true`
it only counts and stores nothing.

### POST /api/units/{id}/variant
Body `{"lang": "vi", "label": "warmer", "preset": "", "style": {...}}`. Queues a **variant** job (kind `variant`,
origin `variant`): the whole episode translated with that style in place of the series' own and the episode's
override, kept as a `candidate` version whose `params.variant` says `{label, preset, style}`. It never goes on disk
by itself and ignores an existing target. At most one variant per episode and language is open at a time (`400`).
Reply `{"ok", "job_id", "label"}`. With `"estimate_only": true` (P12b) the style, the preset and an open variant are checked
the same way, nothing is queued, and the reply is `{"ok", "estimate_only": true, "label", "estimate": {...}}` for the whole episode.

### GET /api/units/{id}/versions/{v}/compare?lang=
The current translation and a version side by side: `{"lang", "version", "status", "params", "model", "aligned",
"differing", "rows": [{"n", "source", "current", "variant", "differs", "judge"?}], "judge"}`.

`judge` on a row (P15) is the LLM judge's advice on that cue, `{"winner": "variant" | "current" | "tie", "why"}`. It is there
only when the judge has looked at exactly these two texts under today's rubric (an edit of the cue, a new variant or a new rubric
version takes it away). `judge` on the answer says how the judge stands for this candidate: `{"enabled", "model",
"prompt_version", "judged", "variant", "current", "tie", "todo", "job"}`, where `todo` counts the differing cues with no verdict
and `job` is the open judge job of this candidate (`{"job_id", "state", "stage"}`) or `null`.

### POST /api/units/{id}/versions/{v}/judge
Body `{"lang": "vi", "again": false, "estimate_only": false}`. Queues a **judge** job (kind `judge`, key
`judge:<unit>:<lang>:<version>`, the interactive class): a model reads the candidate next to the current translation and says,
cue by cue, which text a viewer is better served by (meaning first, then natural speech, then fit; position, length and
formality are no reason). It is advice only (FR-18.8): a verdict never becomes a cue edit and never counts in an A/B score (it
is a `judge` quality signal, not an `ab` one); only `choose` does. The judge asks about the differing cues that have no verdict
yet (`again` asks about all of them), up to 300 cues, in calls of 30 cues each; each batch is stored when it returns, so Stop or
a restart keeps what was paid for and asking again pays only for the rest. The job goes through the one queue: the day's quota,
`max_calls_per_day`, `max_calls_per_job`, the breaker and Stop apply. At most one is open per candidate (a second request
returns that job, `"new": false`). Reply `{"ok", "job_id", "new", "cues", "calls"}`. With `"estimate_only": true` nothing is
queued: `{"ok", "estimate_only": true, "cues", "differing", "capped", "calls", "model", "estimate": {...}}`.
`400` when `judge_enabled` is off (it is by default), when the version is not a candidate, or when there is nothing to judge;
`404` for a version that does not exist. Read the verdicts with `GET …/compare`.

### POST /api/units/{id}/versions/{v}/choose
Body `{"lang": "vi", "picks": {"12": "variant", "13": "current"}, "whole": "variant", "apply": false}`. Records the admin's
verdict on a candidate: one `ab` quality signal per pick (winner `variant`, `current` or `tie`) and one for `whole`, each
carrying the variant's label, style, model and prompt version, so scores add up by style and prompt version. With
`apply` the verdict is acted on: the cues won by `variant` become cue edits of the current translation, and a
`whole` win adopts the candidate (with `"apply_style": true` it also keeps the variant's style, as `adopt` does; `applied`
then has `style`). `400` when the version is not a candidate. Reply `{"ok", "recorded", "applied"}`.

## Review: suggestions for the glossary and the character cards

What a generator proposes (story facts, story memory, the feedback loop, research: the `source`) waits here until the admin
decides (FR-4.4, FR-11.1; [ARCHITECTURE section 6](ARCHITECTURE.md)). Accepting writes the glossary in the database (a
character also writes its card, in the same transaction) and flags the translations made without it as stale; it never
retranslates (FR-5.6). Errors are `{"error": "..."}` with `400` (a payload the glossary refuses names the offending path,
`address_rules[0]: needs 'a_to_b' and/or 'b_to_a'`; a suggestion already decided) or `404`.

A suggestion has an `id`, its series and language, a `kind`, a `source`, the `payload` it would write, its `evidence` and `sources`, a `status` and what was decided about it (`auto_accepted`, `decided_at`, `undoable`, `edited`):

- `kind` is `term` (`payload` is a glossary term: `source`, `target`, optional `aliases`, `locked`, `note`, `from`, `until`),
  `address_rule` (`a`, `b`, `a_to_b`, `b_to_a`, `from`, `until`, `note`) or `character` (`name`, `target`, `gender`,
  `description`, and optionally the character's voice as `card`, a character card, whose name is the character's).
- `source` is `facts`, `memory`, `feedback` or `research`. `evidence` is a list of what shows why (a cue with its
  timecode, `{"episode", "cue", "timecode", "text", "unit_id"}`, or a line of text); `sources` are the URLs of a research answer.
- `status` is `pending`, `accepted` or `rejected`. `auto_accepted` marks one applied by `auto_accept_*`; `undoable` is true
  for an accepted one that changed the glossary; `edited` for one accepted after the admin changed it.
- A suggestion is identified by what it is about, not by how it is worded: a term by its source (and `from`), an address
  rule by the pair of characters (whichever is named first) and `from`, a character by its name, all without case or
  spacing. A series keeps one decision per identity and language, so a rejected suggestion is not proposed again when a model
  words it differently, and an accepted one is skipped. A character's accepted voice is merged into the card the admin
  already has (the fields it says replace theirs; the others stay) and Undo puts the old card back exactly. For
  `auto_accept_terms` two `sources` count as independent only when they are different sites (the registered domain: `www.`,
  a language edition or another page of one site is the same source).

| Method and path | Answer |
|---|---|
| `GET /api/suggestions?status=&series=&source=&lang=&limit=` | The suggestions, the `counts` of each status, the `pending` total and the pending number of each series. Grouped by series (oldest first inside; newest first when `status` is `accepted` or `rejected`). `limit` is at most 1000 (default 500). |
| `GET /api/suggestions/summary` | The counts and the per-series pending numbers, without the items: what the badge on the nav shows (the nav hides Review while all three counts are 0). |
| `POST /api/suggestions/{id}/accept` | Apply a pending one. Optional `"payload"`: the content as the admin edited it (**Edit, then accept**; it is checked like a glossary save, and the stored suggestion becomes the edited one). Optional `"dry_run": true`: nothing is written, the answer only counts. Reply `{"ok", "saved", "unchanged", "stale_now", "stale_after", "would_stale", "suggestion"}`: `would_stale` is how many translations accepting makes stale (the same number the Glossary page shows before it saves), `unchanged` that the glossary already says it. |
| `POST /api/suggestions/{id}/reject` | Reject: the row stays and the same content is never proposed again. Reply `{"ok", "suggestion"}`. |
| `POST /api/suggestions/{id}/undo` | Put back exactly what accepting replaced (the entry it overwrote, the character's earlier card or none) and return the suggestion to `pending`. `400` when the glossary or the card was changed after the accept: a later edit is never overwritten. `"dry_run": true` counts the translations undoing would flag stale. |
| `POST /api/suggestions/bulk` | Accept every pending suggestion of **one** series: `{"series_id", "lang"?, "expect", "dry_run"?}`. `expect` is the number the admin confirmed; if the inbox has changed (`400` "the list changed"), nothing is applied, and a real request without it is refused. `dry_run` returns `{"ok", "saved": false, "count", "stale_now", "stale_after", "would_stale"}` for all of them together. A real request returns `{"ok", "saved", "count", "failed": [{"id", "error"}], "series", "would_stale"}`; one that cannot be applied stays pending and does not stop the rest. |
| `POST /api/suggestions/generate` | Ask the facts generator (P14) for proposals of **one** series and language: `{"series_id", "lang", "estimate_only"?}`. It makes a `suggest` job and answers at once with `{"ok": true, "job_ids": [...], "queued": 1, "merged": 0}` (an open job for the same series and language is not made twice: `merged` is 1 and it is lifted to the admin's class); the queue runs it under the day's quota and the breaker, and the report (`added`, `errors`, …) is the finished job's `report`. It reads the stored facts of the series (it never extracts any), leaves out what the glossary or the inbox already has (any status), and asks the Memory model: one call each for forms of address, terms and characters, at most three. `estimate_only` spends nothing: `{"ok", "estimate_only": true, "episodes", "address_rules", "terms", "characters", "calls"}`. A finished job's `report` has the same numbers plus `"added"`, `"auto_accepted"`, `"errors"` (a kind whose answer was unusable; the others are still stored) and `"outcomes"` (the `propose` report per source); each kind is stored as soon as it comes back, so a job that waits or fails midway keeps what it found and runs again only for the rest. A translation that read new facts leaves one such job behind (origin `facts`, the lowest class). The proposals have `source` `facts` (`address_rule`, `term`) or `memory` (`character`, with a card when the lines show how they speak), `pending`, with `evidence` of `{"episode", "cue", "timecode", "text", "unit_id", "note"}`; an address rule carries `from`/`until` where the relationship changed. `400` without `series_id` or `lang`; `404` for an unknown series. |
| `POST /api/suggestions/from-edits` | Look at what the admin keeps correcting in **one** series and language: `{"series_id", "lang"}` (P15, FR-19.6). It spends no model call, so it runs at once. A swap of one to three words for one to three others that shows in at least three different corrected cues, and whose cues share a word of the source line, becomes a `term` (`source` that word, `target` the admin's wording, `source` of the suggestion `feedback`), `pending`, with the cues as `evidence` (`{"episode", "cue", "unit_id", "text": "<machine> → <yours>"}`) and no research `sources`, so `auto_accept_terms` never applies it. What the series already decided about that term is kept, like for any generator. Reply `{"cues", "without_before", "patterns", "proposed", "unnamed", "report"}`: `cues` the corrected cues it could compare (`without_before`: older corrections that never kept what the machine wrote), `patterns` the repeated swaps, `proposed` those turned into suggestions, `unnamed` those whose cues share no source word (no term to suggest), `report` the `propose` counts (`null` when nothing was proposed). The worker also looks by itself after each cue edit (`suggest_from_edits`, default on; it never fails the edit). `400` without `series_id` or with a bad `lang`; `404` for an unknown series. |

The worker makes the same proposals by itself after a job has extracted facts (`suggest_from_facts`, default on; it never
fails the job).

`auto_accept_terms` (default on), `auto_accept_address_rules` and `auto_accept_characters` (default off) apply a suggestion
of that kind the moment it is proposed ([CONFIGURATION](CONFIGURATION.md#review-and-auto-accept)); it then shows in
`status=accepted` with `auto_accepted: true` and can be undone. A `term` is applied by itself only when two independent
sources (different hosts) agree on it.

## Insights: measures and the golden set

Insights reads the worker's own records (calls, events, quality signals), never a file (P15, FR-19.5, FR-19.6). Both routes only read.

### GET /api/metrics?days=30

`days` is 1 to 365 (default 30; the page offers 7, 30 and 90); `400` for anything else. Days are UTC days and the window ends now.
The answer is `{"window": {"days", "since", "until"}, "operations", "quality", "previous", "by_model", "by_prompt_version", "by_model_prompt", "ab", "daily", "corrections"}`.
A rate is a number from 0 to 1, or `null` when there was nothing to divide by (**no data is not 0**).

- `operations.queue` `{"counts", "depth"}` (live; `depth` is requested + running + waiting) · `providers` (the health of each model, as `/api/status`) ·
  `daily_quota_pause` · `wait_seconds` `{"jobs", "median", "p90"}` (from being asked for to starting, jobs started in the window) ·
  `usage` `{"calls_today", "calls_last_minute", "limits": {"rpm", "tpm", "rpd", "max_calls_per_day"}}` (`0` = no limit; today is the provider's day, UTC) ·
  `calls` `{"total", "by_outcome", "failed", "rate_429", "rate_503"}` (`rate_429` counts `rate_limited` and `quota_exhausted`, `rate_503` counts `unavailable`) ·
  `escalation` `{"jobs", "escalated", "rescued", "rate", "rescue_rate", "window_days"}` (from the job rows, so it reaches back only as far as `retention_*_days`) ·
  `per_episode` `{"episodes", "tokens_in", "tokens_out", "cost"}` (the translation task only; `cost` needs the prices in Settings) · `tokens` · `latency_ms` `{"calls", "mean", "median", "p90"}` ·
  `calls_by_task` (`translate`, `extract`, `judge`, and `research` once P16 exists) · `research_cost` (`0` with none) ·
  `quota_pauses` (days of the window on which the daily quota was spent on every model and the queue waited for the reset) · `breaker_opens` ·
  `jobs` `{"done", "failed", "success_rate", "retries_per_episode"}` (jobs that make translations and finished in the window; a retry is a wait because the provider said no, per episode translated).
  `quota_pauses`, `breaker_opens` and `retries_per_episode` are counted from the day a worker that knows them first ran: an earlier day reads 0.
- `quality.cues_translated`, `episodes` · `repair` `{"calls", "translate_calls", "rate", "cues", "cue_rate"}` (repair and split calls over translate calls) ·
  `cue_edits` `{"cues", "rate"}` (**the share of cues the admin corrected**: distinct cues corrected in the window over cues translated in it) ·
  `retranslations` `{"count", "rate", "reverts"}` · `review` `{"by_kind", "cps", "cps_rate", "too_long", "too_long_rate"}` (what the review of each finished translation found:
  over 20 characters a second, over 42 a line) · `validate` (what the validator saw in a model's reply, by issue).
- `previous`: `{"cue_edit_rate", "repair_rate", "retranslate_rate", "cps_rate", "too_long_rate"}`, the five rates of `quality` over the window just before this one, as long as it
  (so the page can say better or worse). Each is `null` when that window had nothing to divide by, or when it would reach back past 365 days.
- `by_model`, `by_prompt_version` and `by_model_prompt` (one row for each model and prompt version together, both fields set): `{"model"|"prompt_version"|both, "calls", "episodes", "cues", "tokens_in", "tokens_out", "cost", "repair_rate", "cue_edits", "cue_edit_rate", "cps_rate", "too_long_rate", "retranslations"}`.
  A corrected cue belongs to the model and prompt version of the job that wrote it. `retranslations` is `null` by prompt version and by pair (a retranslation names the model it replaces).
  There is no split by style for translations (a call records only the hash of its style); styles are compared through `ab`.
- `ab`: the cue-by-cue verdicts on variants by style label and prompt version, `{"style", "prompt_version", "variant", "current", "tie", "judged", "score"}`; `score` = (variant wins + ties / 2) / judged.
- `daily`: one row for each day of the window (zeros included): `{"day", "episodes", "calls", "failed", "tokens", "cost", "cues_translated", "cue_edits"}`.
- `corrections`: `[{"series_id", "lang", "edits"}]`, for each series and language the cues whose correction still stands, over all time (not the window): the size of its golden set.
  `series_id` is the worker's (`/api/library/series`). A language with no correction is not listed.

The figures are rolled up from the raw tables into `metrics_daily` (one row per UTC day, name and labels) each time they are asked for, so they survive the clean-up of the events they came from
(`events_retention_days`); a day the clean-up has begun to prune is never recomputed from what is left.

### GET /api/series/{id}/golden?lang=vi

The corrected cues of the series in that language (cue edits that still stand: an undone one is not in it), as a **golden set** for `subcore compare --golden`:
`{"golden_version": 1, "series", "lang", "cues": [{"season", "episode", "cue", "source", "text"}]}`, in story order. `lang` is required (`400` without it), `404` for an unknown series.
With `&download=1` the same text is served as a file (`<series-key>.<lang>.golden.json`, an attachment), the link the console offers.
