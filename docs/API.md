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
  "bazarr": {"status": "OK"}
}
```

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
    {"id": "gemini-flash-latest", "name": "gemini-flash-latest"},
    {"id": "gemini-2.5-flash", "name": "gemini-2.5-flash"}
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
  "gst_model": "gemini-flash-latest",
  "gst_fallback_model": "",
  "gst_batch_size": 500,
  "gst_retry_batch_size": 300,
  "gst_paid_quota": false,
  "gst_skip_upgrade": true,
  "gst_quiet": true,
  "gst_progress_log": false,
  "gst_thoughts_log": false,
  "gst_token_report": false,
  "gst_temperature": "0.7",
  "gst_top_p": "0.95",
  "gst_top_k": "40",
  "gst_thinking_budget": "2048",
  "gst_thinking_level": "medium",
  "gst_no_streaming": true,
  "gst_no_thinking": false,
  "gst_context_size": 50,
  "job_settle_seconds": 600,
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
