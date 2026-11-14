# Configuration

## Environment Variables

Copy `.env.example` to `.env` and edit values for your host.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `CONSOLE_USER` | First run | empty | User name of the console account. Only seeds the account on the first run (or under `CONSOLE_PASSWORD_RESET`). |
| `CONSOLE_PASSWORD` | First run | empty | Initial password. Stored only as a scrypt hash in `app.db`; after that the password is changed in Settings and this variable is ignored. Never stored in `config.json`, never returned by the API. |
| `CONSOLE_PASSWORD_RESET` | No | empty | Set to `1` together with `CONSOLE_USER` / `CONSOLE_PASSWORD` and restart to overwrite the stored account (forgotten password). Remove it afterwards: while it is set, every restart resets the account again. |
| `CONSOLE_AUTH` | No | empty | Set to `off` to disable authentication. Any other value keeps it on. |
| `GEMINI_API_KEY` | No | empty | Primary Gemini API key; can also be set in the web console. |
| `GEMINI_API_KEY1` | No | empty | Alias for the primary Gemini key. |
| `GEMINI_API_KEY2` | No | empty | Optional second key; used when the first key's quota is exhausted or rejected. |
| `TMDB_API_KEY` | No | empty | Used to build movie/show/episode descriptions; can also be set in the web console. |
| `BAZARR_URL` | Yes | `http://bazarr:6767` | Bazarr base URL from inside the worker container. |
| `BAZARR_API_KEY` | No | empty | If empty, the worker tries app settings, then Bazarr `config.yaml`. |
| `INSTALL_FFMPEG` | No | `false` | Optional local image build flag. Leave false when Bazarr extracts subtitles. |
| `GST_BAZARR_IMAGE` | No | `ghcr.io/tamvcspk/llm-subtitle-translator:latest` | Image used by `docker compose pull` and `docker compose up`. |
| `MEDIA_ROOT` | Yes | `/mnt/data` | Host media directory mounted as `/media`. |
| `BAZARR_CONFIG_DIR` | Yes | `/opt/docker/bazarr/config` | Host Bazarr config directory. |
| `BAZARR_POSTPROCESS_DIR` | Yes | `/opt/docker/bazarr/postprocess` | Shared post-processing and queue directory. |
| `WORKER_STATE_DIR` | Yes | `/opt/docker/gemini-srt-translator-bazarr` | Worker state, config, logs, and cache. |
| `GST_MODEL` | No | `gemini-3.1-flash-lite` | Default Gemini model. |
| `GST_ONLY_PATHS` | No | empty | Comma-separated folders. When set, nothing outside them is queued or translated. A guard for trying the worker against a real Bazarr; set it for the Bazarr container too (the enqueue script reads it). |
| `GST_MAX_PENDING` | No | `0` | Most jobs allowed to wait, run or be deferred at once; further items are not queued. `0` is no cap. Same guard, same two containers. |
| `GST_BATCH_SIZE` | No | `500` | Ignored since the worker translates with `subcore`, which plans parts itself. |
| `GST_RETRY_BATCH_SIZE` | No | `300` | Ignored, see `GST_BATCH_SIZE`. |
| `LLM_RPM` | No | `10` (`0` with `gst_paid_quota`) | Requests per minute per API key; `0` means unlimited. |
| `LLM_TPM` | No | `0` | Estimated input tokens per minute per API key; `0` means unlimited. |
| `LLM_RPD` | No | `0` | Requests per rolling 24 hours per API key; `0` means unlimited. When reached, that key counts as out of daily quota. |
| `JOB_TIMEOUT_SECONDS` | No | `1800` | Watchdog for one translation job (minimum 60). |
| `DATA_DIR` | No | `STATE_DIR` | Where `app.db` and `backups/subtitles/` (subtitle versions) live. |
| `DB_PATH` | No | `DATA_DIR/app.db` | SQLite database path. |
| `GST_TARGET_LANGUAGE` | No | `Simplified Chinese` | Legacy fallback when a job has no target language. |
| `WORKER_SLEEP_SECONDS` | No | `15` | Delay between queue polling cycles. |
| `HTTP_TIMEOUT_SECONDS` | No | `20` | Timeout for Bazarr and TMDB HTTP calls. |

## Console Authentication

There is one console account and two ways in: the login form at `/login`
(browsers, cookie session) and HTTP Basic Auth (scripts, `curl -u user:password`).
Every request needs one of them, except `GET /login` and `POST /api/auth/login`.

- First run: with `CONSOLE_USER` and `CONSOLE_PASSWORD` set, the account is
  created in `app.db` (password as a scrypt hash). From then on the stored
  account wins and the variables are ignored, so changing `CONSOLE_PASSWORD`
  and restarting does nothing.
- Change the password in **Settings → Console password** (current password
  required, at least 8 characters). Every other session is signed out, and
  scripts must use the new password.
- Forgot it: set `CONSOLE_PASSWORD_RESET=1` with the new `CONSOLE_USER` /
  `CONSOLE_PASSWORD`, restart once, then remove the flag (a warning is logged
  while it is set).
- No stored account and the variables not both set (and no `CONSOLE_AUTH=off`):
  the console and API answer `503` with instructions. The queue worker keeps
  processing jobs, and an error is logged at every start.
- `CONSOLE_AUTH=off`: no authentication, with a warning logged at every start.
  Use only on a trusted network.
- Five wrong logins in a row from one address (form, Basic Auth and password
  change count together) lock that address for five minutes (`429`).
- The session cookie is `HttpOnly`, `SameSite=Strict`, gets `Secure` behind a
  proxy that sends `X-Forwarded-Proto: https`, and expires after 7 days without
  use. It is a signed token (HMAC under a secret stored in `app.db`), so
  sessions survive a worker restart; changing or resetting the password rotates
  the secret and ends every session. Signing out also blocks that cookie until
  the worker restarts. Writes made with a cookie must carry a matching `Origin`.

Credentials are compared in constant time and are never logged. The Bazarr
post-processing script only writes queue files and does not call the HTTP API,
so it needs no credentials. Neither the form nor Basic Auth is encrypted; keep
the port on the LAN or place a TLS reverse proxy in front.

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
  "memory_recent_episodes": 3
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

## Translation Options

The Settings page writes these options into app config:

- `gst_model`: the default model, a light one (`gemini-3.1-flash-lite`) that
  translates every job. Selected from `/api/gemini-models`.
- `gst_fallback_model`: the *strong model* (`gemini-3.5-flash` by default); the
  Settings page calls it "Strong model". A job moves to it when the default
  model cannot finish it: its output stays invalid after repair and splitting
  (a hard job; tried once, then the job fails), the default model stays
  unavailable (`503`) through every delayed retry, or its daily quota is
  exhausted (each Gemini model has its own). The translation starts again on the
  strong model. Choose *None* (empty) to run a single model. A value equal to
  `gst_model` is stored as empty. `/api/status` `model_layers` shows how often
  jobs escalate and how many the strong model finishes; if few escalate or it
  rescues few, drop the layer.
- `gst_paid_quota`: removes the default free-tier request limit (`LLM_RPM`).
- `gst_temperature`: model temperature.
- `gst_thinking_budget`, `gst_no_thinking`: thinking token budget, applied only
  to Gemini 2.5 models; newer models keep their own default.
- `job_settle_seconds`: queue settle window before a new job can run. Default
  is `600` seconds so Bazarr has time to finish extracting other embedded
  subtitles from the same media file.

## Story Memory

Off by default. When on, the worker reads the source subtitles of the episodes before
the one being translated and gives the model a short, deterministic summary of the story
so far: who the characters are, how each pair stands to each other, what the last few
episodes were about. It exists so forms of address (especially in Vietnamese) follow the
story even when Bazarr delivers a season pack out of order. Turn it on after checking the
extraction on a series you know: `uv run subcore memory ep01.srt ep02.srt ... --title Show`
prints the facts and the running relationship state per episode.

- `memory_enabled`: the global switch. A series overrides it on its Episode page
  (Context tab) with *On* or *Off*.
- `memory_gap_wait_seconds` (default `1800`): when an earlier episode has no source
  subtitle yet, the job waits this long (checking every 5 minutes) and then translates with
  the story incomplete (`incomplete` in the Library). `0` never waits. A manual retranslation
  never waits.
- `memory_model`: model that reads facts and condenses old summaries; blank uses `gst_model`.
  A cheaper model is fine.
- `memory_recent_episodes` (default `3`) and `memory_budget_tokens` (default `8000`): the
  summaries of that many latest earlier episodes are given in full; when the whole story
  would exceed the budget, older summaries are condensed by one model call (cached).
- Cost: one extraction call per episode the first time it is needed (cached by the sha256 of
  its subtitle and the extraction prompt version), so the first translated episode of a
  100-episode series reads up to 99 earlier ones. Quota applies as usual: a spent daily
  quota pauses the queue and the facts already read are kept. Use the per-series switch to
  keep long series off.
- Specials (season 0) see the story after the episode set under *Specials see the story
  after* (blank: the end of season 1); their own facts never enter the main storyline.
- A translation is flagged `stale` when the facts of an earlier episode changed or a missing
  one arrived. Nothing is retranslated automatically.

The options of the old `gst` CLI (`gst_batch_size`, `gst_retry_batch_size`,
`gst_skip_upgrade`, `gst_quiet`, `gst_progress_log`, `gst_thoughts_log`,
`gst_token_report`, `gst_top_p`, `gst_top_k`, `gst_thinking_level`,
`gst_no_streaming`, `gst_context_size`) are gone: they had no effect since the
worker translates in-process. An old `config.json` that still contains them is
accepted; the keys are dropped the next time settings are saved.

## What Gets Translated

Only a language that is still missing. A job is dropped, with no model call, when:

- the exact target file already exists, or any sibling subtitle of the same video
  is already in that language (`Name.vi.hi.srt`, `Name.vi.ass`, ...; forced-only
  tracks do not count). The language is matched by its code in the file name;
- Bazarr itself no longer lists the language in the item's `missing_subtitles`
  (checked when the job starts, for jobs that carry Bazarr ids; if Bazarr cannot
  answer, the files above decide). The job ends as `skipped-not-wanted`;
- the folder is not under `GST_ONLY_PATHS`, when that is set.

An explicit retranslation, cue edit or revert is never dropped by these rules.

### Trying the worker against a real Bazarr

Bazarr may report hundreds of items as wanted (a 929-episode backlog is normal)
and runs the post-processing script for every subtitle it downloads, and the
console's "enqueue the scan" button queues up to `scan_limit` files. Before the
first real run, limit the blast radius in **both** containers (the script reads
them in Bazarr's container, the worker re-checks them when it starts a job):

```bash
GST_ONLY_PATHS=/media/Series/One Test Show   # only this folder is ever translated
GST_MAX_PENDING=3                            # at most 3 jobs waiting or running
```

Raise or remove them once the first results look right. Check
`GET /api/queue` (or the Queue page) before leaving it unattended.

## Glossaries and Data Directory

`DATA_DIR` (default: `STATE_DIR`) holds machine-owned data:

```text
app.db                                   versions, library, corrections
backups/subtitles/                       a copy of every published subtitle version
backups/glossary/<series|movies>/<key>/  the previous glossary file before each console save
series/<key>/glossary.<lang>.yaml        series glossary (hand-editable, see below)
movies/<key>/glossary.<lang>.yaml        movie glossary
```

`<key>` is `tvdb-<id>` when Bazarr knows the TVDB id, else `bazarr-<series id>`,
else a slug of the title. A glossary applies to one series and one target
language and may be edited by hand or on the Glossary page. Example:

```yaml
style: casual, keep honorifics
terms:
  - source: Kyber crystal
    target: tinh thể Kyber
    locked: true        # the target must appear whenever the source does
    from: S01E03        # optional window, episode granularity; until: is exclusive
characters:
  - name: Alice
    gender: f
address_rules:
  - a: Alice
    b: Bob
    a_to_b: em–anh      # how Alice addresses Bob
    from: S01E06 00:32:10
```

A glossary file that is not valid stops translation jobs for that series with the
reason in the job error; fix the file (or restore the copy in `backups/glossary/`)
and retry.

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

`code` controls filenames. `language` is the language name sent to the model.

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
