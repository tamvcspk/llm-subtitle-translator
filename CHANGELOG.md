# Unreleased
- **P0c: console login form.** Browsers now sign in at `/login` (cookie session,
  Sign out in the sidebar); Basic Auth keeps working for scripts. The account is
  created from `CONSOLE_USER`/`CONSOLE_PASSWORD` on the first run and stored as a
  scrypt hash (schema 4, table `auth`); from then on the stored account wins and the
  password is changed in Settings (this signs out every other session).
  `CONSOLE_PASSWORD_RESET=1` overwrites it after a forgotten password. Five wrong
  logins lock an address for five minutes. **Upgrade note:** changing
  `CONSOLE_PASSWORD` and restarting no longer changes the password. Sessions live
  in memory, so a restart signs everyone out.
- **P4: story memory (off by default).** Before an episode is translated the worker
  can read the source subtitles of the episodes before it and tell the model who the
  characters are, how each pair stands to each other and what happened lately, so
  forms of address follow the story even when Bazarr delivers a season pack out of
  order. Facts come from source subtitles only (never from translations), are kept
  per subtitle hash, and an episode never sees the facts of a later one: episode 6
  translated first still knows episodes 1-5, episode 1 translated afterwards knows
  nothing of 2-6. When an earlier episode has no subtitle yet the job waits
  (`memory_gap_wait_seconds`, default 30 minutes, re-checked every 5), then
  translates marked `incomplete` and names the missing episodes; when facts of an
  earlier episode change or the missing episode arrives, the later translations are
  flagged `stale` (never retranslated automatically). New **Context** tab on the
  Episode page (what the translation used, what a new one would get, a per-series
  On/Off switch, where specials sit in the story, "Read facts now"), a **Story
  memory** card in Settings (`memory_enabled`, `memory_gap_wait_seconds`,
  `memory_model`, `memory_recent_episodes`, `memory_budget_tokens`), and
  `GET /api/units/{id}/context`, `POST /api/units/{id}/context/extract`,
  `GET/PUT /api/series/{id}/memory`. `uv run subcore memory ep01.srt ep02.srt ...`
  prints the extracted facts and the running relationship state with timecodes, to
  judge extraction quality on a series you know before switching memory on. The
  Checked against Gemini on six episodes each of three real series: the extraction
  prompt now says a fiancé or spouse is `romantic` (it called them `family`) and
  names the exact relationship ("mother and son", "son-in-law and father-in-law")
  because `family` alone cannot tell a parent from a sibling; names like "Ted" and
  "Ted Mosby" are merged into one character when that is unambiguous, but two
  characters of one episode never are; a doubtful reading does not overturn a clear
  one; and the command retries a busy model instead of stopping. The
  database moves to schema 3 (new `facts`, `summaries_cache`, `context_snapshots`
  tables); older databases upgrade in place.
- **Two model layers, light by default.** The default model is now
  `gemini-3.1-flash-lite` and the *strong model* (setting `gst_fallback_model`,
  default `gemini-3.5-flash`) only takes a job the default cannot finish: output
  that stays invalid after repair and splitting now moves the job to the strong
  model once (before, it failed at once); an outage or a spent daily quota still
  hands the job over as before. A job that was escalated records
  `escalated_from` and `escalation_reason`; `/api/status` has `model_layers`
  (finished jobs per model, and how many escalated jobs the strong model rescued)
  so the split can be judged and dropped if it does not pay (clear the strong
  model in Settings to run one model). Existing `config.json` files keep the
  models they already saved.
- **Translate only what Bazarr still misses, and never the whole library.**
  A target language now counts as present when *any* sibling subtitle of that
  video is in it (`Name.vi.hi.srt`, `Name.vi.ass`, ...; a forced-only track does
  not), not only the exact file the worker would write; this applies to the
  Bazarr script, console enqueue, the media scan and the worker. Before a job
  spends a model call the worker asks Bazarr whether the language is still in the
  item's `missing_subtitles`; if Bazarr says it is not, the job ends as
  `skipped-not-wanted` (if Bazarr cannot answer, the files decide). New guards for
  trying the worker against a real Bazarr, read by the worker and by
  `gst_enqueue.sh` (set them in both containers): `GST_ONLY_PATHS` (comma
  separated folders; nothing outside them is queued or translated) and
  `GST_MAX_PENDING` (most jobs waiting or running at once; `0` is no cap).
  Explicit retranslations are not affected.
- **P3: Library, Episode and Glossary pages in the console.** The worker now knows
  which series, episode or movie each job is (Bazarr ids, falling back to the file
  name) and lists them under **Library** with a status per target language:
  `current`, `stale` (the glossary or the episode's corrections changed after the
  translation was made; only flagged, never retranslated automatically),
  `incomplete`, `untranslated`. The **Episode** page shows source and translation
  side by side, jumps to a player time (`00:23:10`) or searches both texts, and
  works on a phone. A cue can be corrected by hand (applied at once as a new
  version, no model call, then locked: later retranslations keep it byte for byte),
  or a rule can be added from it: `term`, `address` (forms of address) or `note`,
  scoped to this episode (stored in `app.db`), from this episode onward, or the
  whole series (both written to the series glossary YAML). **Retranslate** redoes
  the whole episode, a cue range or a time range (the whole source is still sent as
  context, only the range is requested), ahead of automatic jobs, with the rules
  you choose. **History** lists every version with a cue-level diff and one-click
  restore. **Glossary** edits a series' YAML with validation, an atomic write, a
  backup of the previous file and a count of translations a save would make stale.
  New endpoints are in `docs/API.md`; glossary terms accept `from`/`until`.
  The worker reads `DATA_DIR/series/<key>/glossary.<lang>.yaml` for every job.
  **Upgrade notes:** `app.db` migrates to schema 2 on start (adds series, units,
  corrections; existing versions are kept but only appear in Library once their
  episode is translated again). A glossary file that is not valid YAML now stops
  the job with the reason instead of translating without it.
- Settings: removed the options that have had no effect since P2 (`gst_batch_size`,
  `gst_retry_batch_size`, `gst_skip_upgrade`, `gst_quiet`, `gst_progress_log`,
  `gst_thoughts_log`, `gst_token_report`, `gst_top_p`, `gst_top_k`,
  `gst_thinking_level`, `gst_no_streaming`, `gst_context_size`). Saving settings
  drops them from `config.json`. The nav item "Library scan" is now "Media scan".
- **P2: the worker now translates in-process with `subcore` instead of the `gst`
  CLI.** `gemini-srt-translator` is no longer a dependency. Provider errors are
  typed (daily quota, rate limit/overload, rejected key) rather than parsed from
  CLI output. Calls are paced per API key (`LLM_RPM`, `LLM_TPM`, `LLM_RPD`;
  default 10 RPM unless paid quota is enabled), `429`/`503` retry with backoff,
  jitter and `retry-after`, a second API key takes over when the first is spent,
  and a job has a 30 minute watchdog (`JOB_TIMEOUT_SECONDS`).
  **Upgrade notes:** a failed translation now restarts from the beginning (no
  `.partial`/`.progress` resume). The Settings options `gst_batch_size`,
  `gst_retry_batch_size`, `gst_top_p`, `gst_top_k`, `gst_thinking_level`,
  `gst_context_size` and the logging/streaming switches no longer have any effect.
- New SQLite store `app.db` (in `DATA_DIR`, default the state volume) records
  every published subtitle version and every model call. A result is reused
  without a model call when the source, model, temperature and description are
  unchanged. Every version has a copy in `backups/subtitles/`; a retranslate keeps
  hand-edited files as backups first and a revert restores any version exactly.
  Automatic jobs still never overwrite an existing target.
- Removed `scripts/update_upstream.py`; Dependabot now watches `google-genai`.

# 2026-11-12
- `subcore` now detects a model that attaches translations to the wrong cue
  number (seen with `gemini-3.1-flash-lite`: all ids present, text shifted by
  2–4 cues for hundreds of cues). Digit/name anchors and a sliding length
  profile flag the shifted region, which is re-requested through the normal
  repair rounds; a drift that survives them only adds a warning.
- `subcore` default Gemini model is `gemini-3.5-flash` (`gemini-2.5-flash` is
  closed to new API users).

# 2026-11-11
- Added mandatory HTTP Basic Auth for the web console and API (P0b, NFR-5).
  Set `CONSOLE_USER` and `CONSOLE_PASSWORD`; requests without matching
  credentials get `401`, and with credentials unset the console answers `503`
  with instructions while the queue worker keeps running. `CONSOLE_AUTH=off`
  opts out (warning logged at start). **Upgrade note:** existing deployments
  must add the two variables or the console stays locked.
- Added the `subcore` engine (P1, library + CLI only; the worker still runs `gst`):
  SRT parsing/validation/atomic write, a typed-error provider layer (Gemini via
  google-genai, plus a network-free fake), a translator that plans parts at
  silences, sends `{i, text}` only (never timecodes), and repairs just the
  invalid cues (2 rounds, then split), and YAML glossaries with locked terms and
  timed address rules. `subcore translate file.srt --glossary g.yaml` translates
  by hand. NOTICE now carries the MIT attribution for the prompt rules adapted
  from gemini-srt-translator.
- Converted the repository to a uv workspace (P0, no behavior change): the
  worker moved to `apps/worker/` (`llm-subtitle-worker`), a new `subcore`
  engine skeleton lives in `packages/subcore/`. The Docker image installs only
  the worker and keeps the `/app/worker.py`, `/app/gst_worker`, `/app/static`
  runtime layout. CI runs each member's tests separately.
- Fork housekeeping: the default image is now
  `ghcr.io/tamvcspk/llm-subtitle-translator:latest` (compose, `.env.example`,
  README, configuration docs); Dependabot PRs are assigned to the fork owner.

# 2026-09-07
- Centralized queue lifecycle transitions, recovery, retry metadata, quota
  admission, and error-file cleanup in the queue module; the worker supplies
  the translation execution callback.
- Unified translator checkpoint and partial-output handling for execution and
  queue progress snapshots. Added lifecycle and producer-protocol coverage.
- Added log level and text filters, live refresh, follow-latest, collapsible
  error details, filtered download, and explicit current-log clearing.
- Centralized logging in `gst_worker/logs.py` with UTC timestamps, bounded
  reads, rotation, and structured log API responses.
- Removed obsolete single-language helpers and worker re-exports; tests now import
  domain modules directly. Removed legacy batch-profile migration and log API
  `lines` / local-timestamp compatibility. Explicit batch sizes are preserved.
- Removed array-form `targets.json` support; use the object format containing
  `source_languages` and `target_languages`.
- Added subtitle and language context, elapsed time, and readable retry times
  to worker job logs.

# 2026-08-17
- Classified Gemini daily quota and provider-unavailable failures instead of
  treating every `gst` exit 130 as a subtitle-content retry.
- Added deferred queue retries for `503` responses and a persisted 24-hour
  circuit breaker for daily quota exhaustion.
- Changed translation batches to two levels, `500` and `300`, while retaining
  `gemini-flash-latest` as the default model.

# 2026-07-23
- Fixed the dependency constraint test so compatible Dependabot 3.x update PRs
  do not fail CI when the minimum version changes.

# 2026-07-21
- Migrated dependency management from `requirements.txt` and pip to
  `pyproject.toml`, uv, and a reproducible `uv.lock`.
- Upgraded gemini-srt-translator to 3.6.1.
- Added a manual upstream dependency update script.
- Added daily Dependabot dependency update pull requests.
- Added CI checks for the locked environment, test suite, Python compilation,
  and the upstream `gst` CLI.

# 2026-07-01
- Upgraded gemini-srt-translator to 3.5.9 for improved upstream FFmpeg handling
  and correct interrupted-translation exit codes.

# 2026-06-24
- Upgraded gemini-srt-translator to 3.5.8 for upstream package upgrade fixes.

# 2026-06-23
- Added the Docker sidecar worker and Bazarr post-processing enqueue script.
- Added the web console for status, wanted items, local scans, queue, settings,
  logs, backups, and connection tests.
- Added multi-source and multi-target language support using Bazarr's language
  API.
- Added scheduled backups every 7 days with 30-day retention.
- Added secret masking and connection tests for Bazarr, Gemini, and TMDB.
- Added the Gemini model selector and gemini-srt-translator CLI settings.
- Added gemini-srt-translator 3.5.7 token report support.
- Added recommended translation tuning defaults and a configurable 10-minute
  queue settle window.
- Added optional TMDB translation context and Bazarr scan refreshes.
- Added the optional FFmpeg Docker build flag; FFmpeg is disabled by default
  because Bazarr performs subtitle extraction.
- Added project documentation, MIT license, contribution guide, and security
  notes.
- Split backend helpers into modules for config, subtitle paths, queue, Bazarr
  API, TMDB descriptions, translation execution, backups, and HTTP handling.
- Removed the unused Token Stats setting from the web console and translation
  command.
- Fixed the Docker web port mapping to consistently use `6789:6789`.
