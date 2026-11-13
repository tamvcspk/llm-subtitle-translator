# HTTP API

The web console uses these endpoints. They are intentionally small and local to
the worker; do not expose them directly to the internet without a trusted
reverse proxy that adds TLS.

## Authentication

Every endpoint, and every static file, requires HTTP Basic Auth
(`Authorization: Basic base64(user:password)`) using `CONSOLE_USER` and
`CONSOLE_PASSWORD`. See [CONFIGURATION.md](CONFIGURATION.md#console-authentication).

| Situation | Response |
| --- | --- |
| Missing or wrong credentials | `401` `{"error": "authentication required"}` with `WWW-Authenticate: Basic realm="LLM Subtitle Translator"` |
| `CONSOLE_USER` / `CONSOLE_PASSWORD` not configured | `503` `{"error": "..."}` with setup instructions |
| `CONSOLE_AUTH=off` | No check |

```bash
curl -u "$CONSOLE_USER:$CONSOLE_PASSWORD" http://<host>:6789/api/status
```

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
    "gemini_api_key": "**********",
    "gemini_api_key_configured": true,
    "gemini_api_key2": "",
    "gemini_api_key2_configured": false,
    "tmdb_api_key": "**********",
    "tmdb_api_key_configured": true
  },
  "bazarr_url": "http://bazarr:6767",
  "bazarr": {"status": "OK"},
  "model_layers": {
    "by_model": {
      "gemini-3.1-flash-lite": {"done": 41, "failed": 1},
      "gemini-3.5-flash": {"done": 3, "failed": 1}
    },
    "escalated": {"total": 5, "done": 3, "failed": 1, "waiting": 1}
  }
}
```

`model_layers` is read from the job files and shows whether the strong model is
worth having. `by_model` counts translated and failed jobs per model that ran
them last; `escalated` counts jobs the default model could not finish (output
still invalid after repair) that moved to the strong model, split by how they
ended. Few escalations, or a strong model that rescues few of them, means the
second layer is not paying for itself.

## GET /api/queue

Returns all queue states and counts.

Both `/api/queue` and `/api/status` include `daily_quota_pause`: `null` when
inactive, or `{"retry_at": 1790355039.786}` while paused (Unix seconds).
The console shows the expiry in the browser's local time and explains that
cancelling failed jobs does not lift the pause. Provider error details are not
included in this field.

## GET /api/settings

Returns current app settings. Configured secret fields are returned as
`**********`; use the matching `*_configured` booleans to know whether a key is
available.

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

Returns Gemini model choices for the Settings dropdown. If the Gemini API cannot
be reached or no key is configured, the worker returns a bundled fallback list.

```json
{
  "items": [
    {"id": "gemini-3.1-flash-lite", "name": "gemini-3.1-flash-lite"},
    {"id": "gemini-3.5-flash", "name": "gemini-3.5-flash"}
  ]
}
```

## POST /api/settings

Saves app settings and writes enabled source/target languages for the Bazarr
post-processing script. Leave a secret blank or submit `**********` to keep the
current stored key.

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
  "job_settle_seconds": 600,
  "memory_enabled": false,
  "memory_model": "",
  "memory_gap_wait_seconds": 1800,
  "memory_budget_tokens": 8000,
  "memory_recent_episodes": 3,
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

Moves a failed job back to pending.

```json
{"job_id": "7fefefb6fdb4f200bbb5fcac70e917492cea4d8f"}
```

## POST /api/queue/delete

Deletes one job from a queue state.

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

## GET /api/backups

Lists server-side backup zip files under `/state/backups`.

## POST /api/backups

Creates a manual backup zip containing app config, post-processing targets, and
metadata. Secret values may be present inside the zip.

## GET /api/backups/download?name=<backup.zip>

Downloads a backup zip by file name. The `name` value must be a file listed by
`GET /api/backups`; path components are rejected.

## POST /api/backups/import

Imports a backup zip sent as the raw request body with
`Content-Type: application/zip`. The import restores:

- `config/config.json` to `/state/config.json`;
- `postprocess/targets.json` to `/bazarr-postprocess/targets.json`.

The worker creates a `pre-import` backup before replacing either file. Queue
state, logs, and caches are not restored.

## POST /api/test-connection

Tests a configured or form-entered API key without saving it. `kind` can be
`bazarr`, `gemini_api_key`, `gemini_api_key2`, or `tmdb_api_key`.

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

## Library, episodes, corrections, versions and glossaries

These endpoints back the Library, Episode and Glossary pages. Errors are
`{"error": "..."}` with `400` (bad request or invalid YAML) or `404` (unknown
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
`series`; the last two write the series glossary YAML (movie glossary for movies)
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
`{"lang", "modified_outside", "versions": [{"version", "trigger", "status", "parent_version", "model", "sha256", "tokens_in", "tokens_out", "created_at", "current"}]}`, newest first.
`trigger` is `auto`, `retranslate`, `cue_edit`, `revert` or `external` (a hand edit kept before an overwrite).

### GET /api/units/{id}/versions/{v}/diff?against=&lang=
Cue-level changes from `against` (default: the previous version) to `v`:
`{"version", "against", "changed_cues", "changes": [{"op", "before": [{n, index, time, text}], "after": [...]}]}`.

### POST /api/units/{id}/versions/{v}/revert
Body `{"lang": "vi"}`. Restores version `v` byte for byte as a new version (what
was on disk is kept first) and asks Bazarr to rescan. Cue locks whose text is no
longer in the restored file are released. Reply `{"ok": true, "version", "restored"}`.

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
`{"lang", "exists", "yaml", "stale_units"}`. `lang` is required.

### PUT /api/series/{id}/glossary?lang=
Body `{"yaml": "...", "dry_run": false}`. The YAML is validated (`400` names the
offending path), the previous file is kept under `backups/glossary/`, and the new
one is written atomically. Reply `{"ok", "saved", "stale_now", "stale_after", "would_stale"}`;
with `dry_run` nothing is written.
