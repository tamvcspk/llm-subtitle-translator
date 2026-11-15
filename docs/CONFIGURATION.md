# Configuration

## Environment Variables

Copy `.env.example` to `.env` and edit values for your host.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `CONSOLE_USER` | First run | empty | User name of the console account. Only seeds the account on the first run (or under `CONSOLE_PASSWORD_RESET`). |
| `CONSOLE_PASSWORD` | First run | empty | Initial password. Stored only as a scrypt hash in `app.db`; after that the password is changed in Settings and this variable is ignored. Never stored in `config.json`, never returned by the API. |
| `CONSOLE_PASSWORD_RESET` | No | empty | Set to `1` together with `CONSOLE_USER` / `CONSOLE_PASSWORD` and restart to overwrite the stored account (forgotten password). Remove it afterwards: while it is set, every restart resets the account again. |
| `CONSOLE_AUTH` | No | empty | Set to `off` to disable authentication. Any other value keeps it on. |
| `GEMINI_API_KEY` | First run | empty | Primary Gemini API key. **Seeds the stored key on the first run only**; afterwards keys are managed in the web console and this variable is ignored (see [Settings and API keys](#settings-and-api-keys)). |
| `GEMINI_API_KEY1` | First run | empty | Alias for the primary Gemini key (seed). |
| `GEMINI_API_KEY2` | First run | empty | Optional second key (seed); used when the first key's quota is exhausted or the first key is refused. |
| `LLM_PROVIDER` | No | `gemini` | `gemini` or `openai_compat` (an OpenAI-compatible server). Used until a provider is saved in Settings (a saved value wins). See [Second provider](#second-provider-openai-compatible). |
| `LLM_BASE_URL` | No | empty | Base URL of the OpenAI-compatible server, for example `http://ollama:11434/v1` or `https://openrouter.ai/api/v1`. Used until one is saved in Settings. |
| `LLM_API_KEY` | First run | empty | Seed for the first `openai_compat` key (`OPENAI_API_KEY` is the same). Leave it empty for a server that needs no key. |
| `TMDB_API_KEY` | First run | empty | Seed for the TMDB key (movie/show/episode descriptions). |
| `BAZARR_URL` | Yes | `http://bazarr:6767` | Bazarr base URL from inside the worker container. Used while no Bazarr URL is saved in the console. |
| `BAZARR_API_KEY` | First run | empty | Seed for the Bazarr key. With no stored key and no seed, the worker reads Bazarr's own `config.yaml`. |
| `INSTALL_FFMPEG` | No | `false` | Optional local image build flag. Leave false when Bazarr extracts subtitles. |
| `GST_BAZARR_IMAGE` | No | `ghcr.io/tamvcspk/llm-subtitle-translator:latest` | Image used by `docker compose pull` and `docker compose up`. |
| `MEDIA_ROOT` | Yes | `/mnt/data` | Host media directory mounted as `/media`. |
| `BAZARR_CONFIG_DIR` | Yes | `/opt/docker/bazarr/config` | Host Bazarr config directory. |
| `BAZARR_POSTPROCESS_DIR` | Yes | `/opt/docker/bazarr/postprocess` | Shared post-processing and queue directory. |
| `WORKER_STATE_DIR` | Yes | `/opt/docker/gemini-srt-translator-bazarr` | Worker state, config, logs, and cache. |
| `GST_MODEL` | No | `gemini-3.1-flash-lite` | Default Gemini model, used until a model is saved in Settings (a saved value wins). |
| `GST_ONLY_PATHS` | No | empty | Comma-separated folders. When set, nothing outside them is queued or translated. A guard for trying the worker against a real Bazarr; set it for the Bazarr container too (the enqueue script reads it). |
| `GST_MAX_PENDING` | No | `0` | Most jobs allowed to wait, run or be deferred at once; further items are not queued. `0` is no cap. Same guard, same two containers. |
| `GST_BATCH_SIZE` | No | `500` | Ignored since the worker translates with `subcore`, which plans parts itself. |
| `GST_RETRY_BATCH_SIZE` | No | `300` | Ignored, see `GST_BATCH_SIZE`. |
| `LLM_RPM` | No | `10` (`0` with `gst_paid_quota`, and `0` with `openai_compat`) | Requests per minute per API key; `0` means unlimited. Only Gemini's free tier has a default limit. |
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
| `APP_CONFIG_PATH` | `/state/config.json` | The settings file of versions before P6. Read **once**, on the first start with the database, to import its values and keys; never written afterwards. |
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

## Settings and API keys

Settings and API keys live in the SQLite database (`app.db`, tables `settings` and `api_keys`), not in a file
(FR-20). The web console edits them and a change is **live at once**: the running worker adopts it when it is
saved, with no restart.

**Order of precedence** for a setting: the value saved in the console, then the environment variable, then the
default. Only a few settings have an environment variable (`BAZARR_URL`, `GST_MODEL`, `LLM_PROVIDER`,
`LLM_BASE_URL`, `LLM_RPM`, `LLM_TPM`, `LLM_RPD`); a setting you clear in the console falls back to it.

**The environment holds the configuration of the process itself**: paths (`STATE_DIR`, `QUEUE_DIR`, `DATA_DIR`,
`DB_PATH`, `LOG_DIR`, ...), the web address and port, and the console account (`CONSOLE_USER`,
`CONSOLE_PASSWORD`). These are read when the worker starts and are never stored in the settings table, and the
console cannot change them.

**First start with the database** (an upgrade from a version that kept `/state/config.json`):

- The values in `config.json` are imported into `settings` once. A value already in the table is kept, and
  importing is never repeated, so editing the old file later changes nothing. The file is left in place and is
  no longer read; you can delete it. Options of the removed `gst` CLI are dropped.
- The keys in `config.json` (and, for a field it leaves empty, the `GEMINI_API_KEY*`, `TMDB_API_KEY` and
  `BAZARR_API_KEY` variables) become rows of `api_keys`, once. Afterwards the database wins: a changed or removed
  variable does not overwrite or bring back a key. To change a key, use the console.

**API keys.** Each key has a provider (`gemini`, `openai_compat`, `bazarr`, `tmdb`), a label, a status and the time
it was last used. The Settings page edits them through four fields (Bazarr, TMDB, Gemini 1, Gemini 2); the key of
the second provider has its own field in the settings API (`openai_compat_api_key`); `/api/keys` works on every
stored key. The status is `ok`, `invalid` or `exhausted`:

- A Gemini key the provider refuses (HTTP 401/403, or 400 with the reason `API_KEY_INVALID`) becomes `invalid`:
  it is passed over, the next key takes over, and Settings and the dashboard show a warning. With no usable key
  left, jobs fail with a message that says the keys were refused (from P8 they wait instead).
- Pasting a new key into the same field, or **Test** on a key that works, sets it back to `ok`; the next call
  uses it.
- A model the provider does not know (HTTP 404) is a warning about the model setting, not about a key.
- Runtime uses the first two usable Gemini keys (oldest first) and every usable key of `openai_compat`, in the
  order they were added; the next key takes over when one is refused or its quota is spent.

**Protection.** The API returns only the last four characters of a key (and nothing for a key shorter than 12
characters). Keys are filtered from the log, events, prompt bodies and job error files. The database file is
created with mode `0600`. Keys are not encrypted in the database: the worker has to read them back, and a key
stored beside the database would add little. **Backups** hold no key unless asked to (see [Backup](#backup)).

### Settings

Saved settings with their defaults (the Settings page shows the same fields; keys are not part of this list):

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
  "job_settle_seconds": 600,
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
  "llm_cost_out_per_mtok": 0.0
}
```

When settings are saved, enabled source and target languages are also written to:

```text
/bazarr-postprocess/targets.json
```

That file is read by Bazarr's post-processing script, so automatic Bazarr events
and manual console jobs use the same translation language rules.

The public settings API returns each key field as `**********` plus `*_configured`,
`*_last4` and `*_status`, never the stored value. Saving `**********` (or an empty
field) keeps the stored key.

## Second provider (OpenAI-compatible)

Gemini is the default. Any server that speaks the OpenAI *chat completions* API can run the models instead
(FR-9.1): OpenRouter, Ollama, LM Studio, vLLM, OpenAI itself. Switch in Settings (or the settings API); it is live
at once, and the next call goes to the new provider. One provider serves everything at a time: both model layers
and every task.

- `llm_provider`: `gemini` (default) or `openai_compat`.
- `llm_base_url`: the address up to `/v1`, for example `http://ollama:11434/v1`. An address ending in
  `/chat/completions` is accepted too. It must start with `http://` or `https://` and carry no user name or
  password. Redirects are never followed, because the key would go with them.
- The key: add it like any other (`openai_compat_api_key` in the settings form, or `POST /api/keys` with
  `"provider": "openai_compat"`). **A server of your own needs none**: with no key stored, requests go without
  an `Authorization` header. **Test** on the key asks the server for its model list (`GET {base}/models`).
- The model names are the server's own (`llama3.1:8b`, `qwen/qwen3-32b`). The defaults of `gst_model` and
  `gst_fallback_model` are Gemini models: **when you switch provider, set both**, or clear the strong model
  (`None`) to run one model. A name the server does not know shows as a warning in Settings and fails the job
  with that explanation; it never marks the key invalid.
- `llm_max_input_tokens` (default `128000`) and `llm_max_output_tokens` (default `8192`): the server cannot say what
  its model allows, so set the real limits. The output limit decides how an episode is cut into parts; a small
  local model wants `4096` or less.
- Structured output degrades by capability. The worker first asks for a JSON schema; a server that answers 400/422
  to it is asked for plain JSON mode, then for JSON through the prompt alone, and the choice is remembered for that
  server and model until the worker restarts. Every reply is checked cue by cue as with Gemini, so a weaker server
  costs more repair calls, not wrong subtitles.
- Limits: `LLM_RPM`, `LLM_TPM`, `LLM_RPD` apply per key; with `openai_compat` they default to unlimited. A `429`
  without a quota code is a rate limit (retried with backoff, honouring `Retry-After`); `insufficient_quota` and
  `402` (out of credit) count as spent quota: that model is paused for 24 hours, exactly as when Gemini's daily
  quota is spent. The job waits until the reset (P8: a breaker per model, see `breaker_threshold`).
- Gemini-only features stay with Gemini: `web_search` (the research task, P16) is not offered by this adapter,
  and the thinking-token settings do nothing here.

## Translation Options

The Settings page saves these options:

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
- `research_model`: model of the research task (FR-7, P16); blank uses `gst_model`. Nothing calls it yet.
- `gst_paid_quota`: removes the default free-tier request limit (`LLM_RPM`).
- `gst_temperature`: model temperature.
- `gst_thinking_budget`, `gst_no_thinking`: thinking token budget, applied only
  to Gemini 2.5 models; newer models keep their own default.
- `job_settle_seconds`: queue settle window before a new job can run. Default
  is `600` seconds so Bazarr has time to finish extracting other embedded
  subtitles from the same media file.
- `breaker_threshold` (default `3`, min `1`): consecutive rate-limit or
  unavailable errors, after the retries inside one call, that open the breaker of
  a model. An open breaker rests 300, 900, 1800, then 3600 seconds, then one trial
  call; a job waiting on it does not fail.
- `max_crashes` (default `3`): a job found running after this many restarts is
  failed with reason `crashed`.
- `max_calls_per_job` (default `20`, `0` = no cap): model calls one job may make.
  Reaching it fails the job with the reason.
- `max_calls_per_day` (default `0`, no cap): model calls all jobs may make in one UTC
  day. Reaching it makes jobs wait until the next UTC midnight. Turn it on with a paid key.
- `transient_retry_seconds` (default `30`): how soon a job runs again after one failure
  that did not open the breaker.
- `edit_coalesce_minutes` (default `10`, min `0`): cue edits made closer than this to the
  previous edit are one `edit` version (FR-16.7). `0` makes every save its own version.
- `quota_reset_tz` (default `America/Los_Angeles`): the zone whose midnight resets a
  daily quota. Gemini counts days in Pacific time; check it against the provider's
  documentation before relying on it (SPEC section 8).
- `admission_mode`: what the worker does with jobs the Bazarr script queues.
  `auto` (default) translates them. `hold` receives and stores them as *held*
  jobs without calling the model; you approve or reject them in Queue → Held.
  Jobs you create in the console (Enqueue, Scan, retranslate, cue edit, revert)
  are never held. Changing the mode does not move jobs already waiting or held:
  switching to `auto` does not release held jobs, and switching to `hold` does
  not hold what is already pending. `GST_ONLY_PATHS` still applies; held jobs
  are not counted by `GST_MAX_PENDING`, and approving is not limited by it.
  `gst_enqueue.sh` is the same in both modes.

## Logging and Measurement

The worker keeps a trace of what it decides and what it asks the model, so you can answer
"why did this episode not run?" and later tell which prompt or model did better. Settings →
*Logging and measurement* (all take effect when saved, no restart):

- `log_level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default `INFO`): the level of
  `worker.log`, and the lowest level of event that is recorded. Subtitle text appears in the
  log only at `DEBUG`. Each event is also one JSON line in the log (`EVENT {...}`).
- `log_prompt_bodies` (`all` default, `sample`, `errors`, `off`): which model calls keep their
  prompt and answer. `errors` keeps failed calls only; `sample` keeps failed calls and one in
  ten; `off` writes no body file at all. The call record itself (tokens, latency, outcome,
  prompt version, ...) is always kept.
- Bodies are JSONL files in `$DATA_DIR/logs/llm/` (`<day>.<n>.jsonl`, gzipped once closed),
  one line per call. A line is cut to `log_body_max_bytes` (default `262144`, at least `1024`)
  by shortening its longest text; `log_max_mb` (default `500`) and `log_retention_days`
  (default `30`) bound the folder: the oldest closed files are deleted past either limit, and
  `0` turns that limit off. Disk use is also bounded because a file is closed at a quarter of
  `log_max_mb` (at most 16 MiB).
- `events_retention_days` (default `90`; `0` keeps events forever): older rows of the event
  log are deleted.
- `llm_cost_in_per_mtok`, `llm_cost_out_per_mtok` (default `0`): the price of a million input
  and output tokens, in your currency, used only to fill `cost_est` on each call record.
  `0` leaves the estimate at zero.

Keys, passwords, cookies and `Authorization` headers are removed from the log, the events, the
bodies and error text. A key is recognized by its value (the configured ones) and by its shape
(`AIza...`, `Bearer ...`, `Cookie:`, `password=...`); prompt bodies skip the `password=` rule
because dialogue may say it. Recording is best effort: a full disk or a locked database drops
the record (counted in `GET /api/events` `telemetry.dropped`) and never fails a job.

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
- `memory_model`: model that reads facts (task `extract`); blank uses `gst_model`. A cheaper model is fine.
- `summarize_model`: model that condenses old summaries (task `summarize`); blank uses `memory_model`, then
  `gst_model`.
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
accepted; the importer leaves them out.

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
config/config.json            the saved settings, without any API key
config/api_keys.json          only when the backup was made with "Include the API keys"
postprocess/targets.json
```

A backup **holds no API key** unless you tick *Include the API keys in this backup* on the Backups page (or send
`{"include_keys": true}` to `POST /api/backups`); a file made that way is plain text, so keep it as private as the
keys. Scheduled backups never include keys. Neither the console account (user and password hash) nor any other part
of the database is in a backup.

The UI can download backup zip files and import a selected backup zip. Import replaces the saved settings with the
backup's, adds the keys of the backup when it has them (a key the worker already has is not duplicated), and
writes `postprocess/targets.json`. It never restores the console account, creates a `pre-import` backup first
(without keys), and does not restore queue files, logs, or caches. A backup made before P6 carries its keys inside
`config/config.json`; import uses them only for a key field that is empty now.

Because a backup holds no key, back up the database too (it holds the keys, the account and the history) when you
need to move the worker to a new host:

```bash
/opt/docker/gemini-srt-translator-bazarr/.env
/opt/docker/gemini-srt-translator-bazarr/app.db
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
