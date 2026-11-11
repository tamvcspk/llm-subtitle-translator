# Configuration

## Environment Variables

Copy `.env.example` to `.env` and edit values for your host.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `CONSOLE_USER` | Yes | empty | Username for the web console and API (HTTP Basic Auth). |
| `CONSOLE_PASSWORD` | Yes | empty | Password for the web console and API. Read from the environment only; never stored in `config.json`, never returned by the API. |
| `CONSOLE_AUTH` | No | empty | Set to `off` to disable authentication. Any other value keeps it on. |
| `GEMINI_API_KEY` | No | empty | Primary Gemini API key used by `gemini-srt-translator`; can also be set in the web console. |
| `GEMINI_API_KEY1` | No | empty | Alias for the primary Gemini key. |
| `GEMINI_API_KEY2` | No | empty | Optional second key passed through to `gemini-srt-translator`. |
| `TMDB_API_KEY` | No | empty | Used to build movie/show/episode descriptions; can also be set in the web console. |
| `BAZARR_URL` | Yes | `http://bazarr:6767` | Bazarr base URL from inside the worker container. |
| `BAZARR_API_KEY` | No | empty | If empty, the worker tries app settings, then Bazarr `config.yaml`. |
| `INSTALL_FFMPEG` | No | `false` | Optional local image build flag. Leave false when Bazarr extracts subtitles. |
| `GST_BAZARR_IMAGE` | No | `ghcr.io/tamvcspk/llm-subtitle-translator:latest` | Image used by `docker compose pull` and `docker compose up`. |
| `MEDIA_ROOT` | Yes | `/mnt/data` | Host media directory mounted as `/media`. |
| `BAZARR_CONFIG_DIR` | Yes | `/opt/docker/bazarr/config` | Host Bazarr config directory. |
| `BAZARR_POSTPROCESS_DIR` | Yes | `/opt/docker/bazarr/postprocess` | Shared post-processing and queue directory. |
| `WORKER_STATE_DIR` | Yes | `/opt/docker/gemini-srt-translator-bazarr` | Worker state, config, logs, and cache. |
| `GST_MODEL` | No | `gemini-flash-latest` | Model passed to `gst translate --model`. |
| `GST_BATCH_SIZE` | No | `500` | Batch size passed to `gst translate --batch-size`. |
| `GST_RETRY_BATCH_SIZE` | No | `300` | Smaller batch used only after a subtitle line-count/content error. Set `0` to disable. |
| `GST_TARGET_LANGUAGE` | No | `Simplified Chinese` | Legacy fallback when a job has no target language. |
| `WORKER_SLEEP_SECONDS` | No | `15` | Delay between queue polling cycles. |
| `HTTP_TIMEOUT_SECONDS` | No | `20` | Timeout for Bazarr and TMDB HTTP calls. |

## Console Authentication

Every request to the web console (static files and `/api/*`, all methods) needs
HTTP Basic Auth. There are no exempt paths.

- `CONSOLE_USER` and `CONSOLE_PASSWORD` both set: requests without the matching
  `Authorization` header get `401` with `WWW-Authenticate: Basic`. Browsers
  remember the login, so the console has no separate login page; scripts send
  the header (`curl -u user:password ...`).
- Either one missing (and no `CONSOLE_AUTH=off`): the console and API answer
  `503` with instructions. The queue worker keeps processing jobs, and an error
  is logged at every start.
- `CONSOLE_AUTH=off`: no authentication, with a warning logged at every start.
  Use only on a trusted network.

Credentials are compared in constant time and are never logged. The Bazarr
post-processing script only writes queue files and does not call the HTTP API,
so it needs no credentials. Basic Auth is not encrypted; keep the port on the
LAN or place a TLS reverse proxy in front.

The Docker web port is fixed as `6789:6789` in `docker-compose.yml` so the
container listen port and host port stay aligned.

`MEDIA_ROOT`, `BAZARR_CONFIG_DIR`, `BAZARR_POSTPROCESS_DIR`, and
`WORKER_STATE_DIR` are host paths. The container sees them as `/media`,
`/bazarr-config`, `/bazarr-postprocess`, and `/state`.

## Container Runtime Variables

These variables have container-safe defaults and normally do not need to be set,
including with direct `docker run`. Override them only when you intentionally
change the container paths or web bind address.

| Variable | Default in container | Description |
| --- | --- | --- |
| `BAZARR_CONFIG_PATH` | `/bazarr-config/config.yaml` | Bazarr config file used as a fallback source for the Bazarr API key. |
| `QUEUE_DIR` | `/queue` | Queue directory shared with the Bazarr post-processing script. |
| `STATE_DIR` | `/state` | Base state directory for config, logs, cache, and backups. |
| `LOG_DIR` | `/state/logs` | Worker log directory. |
| `APP_CONFIG_PATH` | `/state/config.json` | Web-console app settings file. |
| `POSTPROCESS_TARGETS_PATH` | `/bazarr-postprocess/targets.json` | Source/target language rules read by the Bazarr enqueue script. |
| `TMDB_CACHE_PATH` | `/state/cache/tmdb_cache.json` | TMDB metadata cache file. |
| `WEB_HOST` | `0.0.0.0` | Web console bind address inside the container. |
| `WEB_PORT` | `6789` | Web console port inside the container. |

Equivalent `docker run` skeleton:

```bash
docker run -d \
  --name gemini-srt-translator-bazarr \
  --restart unless-stopped \
  -p 6789:6789 \
  -e CONSOLE_USER=admin \
  -e CONSOLE_PASSWORD='choose-a-password' \
  -v /mnt/data:/media \
  -v /opt/docker/bazarr/config:/bazarr-config:ro \
  -v /opt/docker/bazarr/postprocess:/bazarr-postprocess \
  -v /opt/docker/gemini-srt-translator-bazarr:/state \
  -v /opt/docker/bazarr/postprocess/queue:/queue \
  ghcr.io/tamvcspk/llm-subtitle-translator:latest
```

This starts with default settings. Open the web console to enter Bazarr, Gemini,
and TMDB settings. If you want to preload environment variables, create the env
file first and add `--env-file /opt/docker/gemini-srt-translator-bazarr/.env`
to the command.

When `BAZARR_URL` points to another container by name, add `--network` so Docker
DNS can resolve that name. If the worker is not on Bazarr's Docker network, use
a LAN URL in `BAZARR_URL`.

## Remote Image Updates

The published image is built by GitHub Actions and pushed to GitHub Container
Registry. Standard update flow:

```bash
docker compose pull
docker compose up -d
```

For non-Compose deployments:

```bash
docker pull ghcr.io/tamvcspk/llm-subtitle-translator:latest
docker rm -f gemini-srt-translator-bazarr 2>/dev/null || true
# run the docker run command again with the same env and volume options
```

The workflow also publishes immutable `sha-<commit>` tags. Use one of those tags
instead of `latest` when you want a pinned rollback target.

## App Settings

The web console writes app settings to:

```text
/state/config.json
```

Default:

```json
{
  "source_languages": [
    {"code": "en", "language": "English", "enabled": true}
  ],
  "target_languages": [
    {"code": "zh", "language": "Simplified Chinese", "enabled": true}
  ],
  "media_roots": ["/media"],
  "scan_limit": 200,
  "bazarr_url": "",
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
  "job_settle_seconds": 600
}
```

When settings are saved, enabled source and target languages are also written to:

```text
/bazarr-postprocess/targets.json
```

That file is read by Bazarr's post-processing script, so automatic Bazarr events
and manual console jobs use the same translation language rules.

Secrets are stored in `/state/config.json` if configured from the web console.
The public settings API returns configured secrets as `**********` plus
`*_configured` booleans, never the stored secret values. Saving `**********`
keeps the existing secret.

## gemini-srt-translator Options

The Settings page writes these `gst translate` options into app config:

- `gst_model`: selected from `/api/gemini-models`; passed to `--model`.
- `gst_fallback_model`: optional second model, empty by default. A job moves to
  it after the primary model stays unavailable (`503`) through every delayed
  retry, or when the primary model's daily quota is exhausted; each Gemini model
  has its own daily quota. The job resumes from its checkpoint. A value equal to
  `gst_model` is stored as empty.
- `gst_batch_size`: passed to `--batch-size`.
- `gst_retry_batch_size`: used only when `gst` reports invalid subtitle content,
  such as an output line-count mismatch. Provider `429` and `503` errors never
  reduce the batch size or trigger an immediate subprocess retry. Set to `0` to
  disable the content fallback.
- `gst_paid_quota`: enables `--paid-quota`.
- `gst_skip_upgrade`: enables `--skip-upgrade`.
- `gst_quiet`: enables `--quiet`.
- `gst_progress_log`: enables `--progress-log`.
- `gst_thoughts_log`: enables `--thoughts-log`.
- `gst_token_report`: enables `--token-report`, which writes the upstream
  token and cost report JSON next to the input subtitle by default.
- `gst_temperature`, `gst_top_p`, `gst_top_k`: model tuning values.
- `gst_thinking_budget`, `gst_thinking_level`: thinking controls.
- `gst_no_streaming`, `gst_no_thinking`: boolean CLI switches.
- `gst_context_size`: preceding subtitle lines supplied as translation context;
  defaults to `50`, while `0` disables context.
- `job_settle_seconds`: queue settle window before a new job can run. Default
  is `600` seconds so Bazarr has time to finish extracting other embedded
  subtitles from the same media file.

## Backup

The web console has a manual `Backup Now` action. The worker also creates a
scheduled backup every 7 days and deletes backup zip files older than 30 days.
It writes zip archives to:

```text
/opt/docker/gemini-srt-translator-bazarr/backups
```

Each zip contains:

```text
backup.json
config/config.json
postprocess/targets.json
```

`config/config.json` can contain Bazarr, Gemini, and TMDB secrets. The UI can
download backup zip files and import a selected backup zip. Import restores only
`config/config.json` and `postprocess/targets.json`; it creates a `pre-import`
backup first and does not restore queue files, logs, or caches.

For a full personal deployment backup, also back up:

```bash
/opt/docker/gemini-srt-translator-bazarr/.env
/opt/docker/gemini-srt-translator-bazarr/config.json
/opt/docker/bazarr/postprocess/targets.json
/opt/docker/gemini-srt-translator-bazarr/backups
```

`/state/logs` and `/queue` are useful for troubleshooting but are runtime state,
not required configuration.

## Languages

The settings page loads source and target choices from Bazarr's
`/api/system/languages` endpoint.

`code` controls filenames. `language` is passed to gemini-srt-translator.

Output examples:

```text
Episode.en.srt -> Episode.zh.srt
Episode.ja.srt -> Episode.zh.srt
Episode.zh.srt -> Episode.en.srt
```

## Command-Line Modes

The default command starts both the web console and the worker loop:

```bash
python /app/worker.py
```

Other modes:

```bash
python /app/worker.py --once
python /app/worker.py --worker-only
python /app/worker.py --no-worker
```

`--once` is useful for tests or one-shot cron-style operation.
