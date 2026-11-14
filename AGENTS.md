# Project Collaboration Rules

## Fork Status

This repository is `tamvcspk/llm-subtitle-translator`, a fork of `kingzleshe/gemini-srt-translator-bazarr` (remote `upstream`, push disabled). The fork is being redesigned; the target design lives in `docs/design/SPEC.md` and `docs/design/ARCHITECTURE.md` and takes precedence over upstream docs when they conflict. `docs/ARCHITECTURE.md` describes the inherited (current) code.

- Pull upstream fixes by cherry-pick only; never push to `upstream`.

## Runtime Environment

- Deployment target for this fork is not set up yet. The upstream author's host details (`192.168.1.1`, SSH as root) do not apply to this fork; do not connect to them.
- Default container paths are unchanged from upstream: `/state`, `/queue`, `/media`, `/bazarr-postprocess`, web port `6789`.

## Build and Deployment

- Images are built through GitHub. The default scope is to modify repository code, run relevant tests, and report results; do not build images locally or on the remote device.
- Use read-only SSH checks when the live service state is needed. A code change request does not authorize deployment. Modify container files, replace remote scripts, or restart or recreate containers only when the user explicitly requests an update to the running service.
- Deploy using images built through GitHub. If the user explicitly requests a temporary direct container modification, back up the affected files first and explain that a normal restart preserves the changes, while recreating the container from an image overwrites changes made inside the container.
- Clearly distinguish repository code changes, completed GitHub image builds, and running container updates in reports. Claim only states that have been verified.

## Development Commands

The repo is a **uv workspace** (virtual root, one `uv.lock`): `apps/worker` (`llm-subtitle-worker`: `worker.py`, `gst_worker/`, `static/`, `tests/`, not packaged) and `packages/subcore` (`subcore` engine library, `src/` layout). Install with `uv sync --locked --all-packages`; add deps with `uv add --package <member> <dep>`. Tests use stdlib `unittest`, not pytest; each member's tests run in a separate process. CI (`.github/workflows/ci.yml`) runs:

```bash
uv run --locked python -m unittest discover -s apps/worker/tests -t apps/worker -v
uv run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
uv run --locked python -m compileall -q -f apps/worker/worker.py apps/worker/gst_worker packages/subcore/src
uv run --locked subcore --help
```

Run one module or test by dotted name (from the member directory):

```bash
uv run --locked --directory apps/worker python -m unittest tests.test_worker_queue
uv run --locked --directory apps/worker python -m unittest tests.test_worker_queue.TestWorkerQueueTests.test_enqueue_translation_jobs_for_enabled_targets
```

The Docker image installs only `llm-subtitle-worker` (which depends on `subcore`, so `packages/subcore/src` and its README are copied too) and keeps the old runtime layout (`/app/worker.py`, `/app/gst_worker`, `/app/static`). Every new workspace member needs its `pyproject.toml` copied in the `Dockerfile` before `uv sync --locked`.

Local console preview on Windows: `scripts/start-local.ps1 [-Demo] [-NoWorker]` runs against an isolated `local-state/` directory instead of the Docker `/state` and `/queue` mounts. `-NoWorker` keeps the process from consuming queue jobs.

`subcore` has a manual CLI for judging translation quality without the worker: `uv run subcore translate file.srt --glossary g.yaml` (key from `GEMINI_API_KEY`; `--provider fake` for a dry run).

## Architecture

Read `docs/ARCHITECTURE.md` before changing queue, translation, or Bazarr behavior; it is the source of truth for module responsibilities, queue layout, and job shape. Domain vocabulary (translation job, translation attempt, translation version, daily quota pause, queue lifecycle) is defined in `CONTEXT.md`; use those terms in code and reports. HTTP endpoints are in `docs/API.md`, settings in `docs/CONFIGURATION.md`.

The end-to-end flow spans three independent pieces:

1. `bazarr-postprocess/gst_enqueue.sh` runs inside the Bazarr container, writes one queue JSON per missing target, and returns fast. It stays dependency-free and network-free; its embedded Python payload is tested against the worker's admission and retry protocol, so queue format changes must update both sides.
2. `apps/worker/worker.py` + `gst_worker/` consume the queue one job at a time, translate in-process with `subcore`, write the target `.srt` beside the media, and ask Bazarr to rescan.
3. `apps/worker/static/` is a dependency-free web console that talks only to the worker's own API.

`packages/subcore` must stay free of Bazarr, queue, and HTTP knowledge (ADR-2); the worker depends on it, never the reverse. Roadmap phases (P0, P0b, P1–P5), their checklists, and status are in `PLAN.md`; tick items there as work lands.

Seams to respect when adding code:

- `RuntimeContext` (`gst_worker/runtime.py`) is the composition seam for the running process. New runtime wiring depends on its typed fields; the legacy module-level functions are compatibility entry points for older callers and tests.
- `queue_policy.py` holds pure retry/quota decisions; filesystem moves stay in `queue.py` (`JobQueue`). The HTTP layer reaches the queue only through `ConsoleActions`.
- `TranslationAttempt` (`translation_attempt.py`) is the only place that calls `subcore` and maps its typed provider errors (`QuotaExhausted`, `RateLimited`/`Unavailable`, ...) to the queue's `DailyQuotaExceededError` / `ProviderUnavailableError`; never classify failures by parsing error text. Progress reaches the queue through the status callback (`progress_done`/`progress_total`).
- Core modules reach Bazarr and TMDB only through the `MediaLibrary` and `Metadata` ports (`ports.py`); `worker.py` builds the adapters (`BazarrMedia`, `TmdbMetadata`) and passes them down. Enforced by `tests/test_core_boundary.py`, which also keeps `worker.py` (the composition root) free of direct socket, SQLite and HTTP-server imports. The HTTP handler lives in `gst_worker/web.py` and the settings loader in `gst_worker/settings.py`, both adapters; the handler only parses, authenticates, calls a use case (`ConsoleActions`, `UnitService`) and formats the answer, so queue and job-building logic goes in `console.py`, not in the handler. Upstream cherry-picks that touch the handler in `worker.py` must be re-applied to `web.py`.
- Target subtitles are published only through `TranslationVersions` (`versions.py`), which records each version in the SQLite `Store` and keeps a copy under `DATA_DIR/backups/subtitles/`. An automatic job never overwrites an existing target; retranslate and revert back up a non-system file first.
- Queue recovery runs explicitly at worker startup; creating a `JobQueue` handle must not recover in-flight work.
- Library, corrections, retranslation, history and glossary pages reach the worker through `UnitService` (`units.py`, in `RuntimeContext.units`) and the pure router `library_api.py`; `library.py` knows units and statuses, `rules.py` (pure) merges the glossary file with episode corrections and hashes them (a different hash than the stored `glossary_hash` means stale), `glossaries.py` owns the YAML files. `TranslationAttempt` takes the `Library` so a job is registered and prompted with its rules. Retranslation jobs are queue files with `trigger: retranslate` and `priority: 100`; `should_skip_job` lets them overwrite and `_ready_job_path` runs them first.

- Story memory (P4): `subcore.memory` is pure (facts, fold, render); `gst_worker/memory.py` (`StoryMemory`) decides which facts a unit sees, reads prerequisites inline, waits on gaps (`ContextGapError` → the queue defers the job) and says when a version is stale. "Facts of a unit" is always the newest stored facts for the current `EXTRACT_PROMPT_VERSION`, for building a bundle and for checking staleness alike; keep it that way. The attempt asks memory for sections only through `StoryMemory.plan`, and maps provider errors raised while extracting exactly as for translating. Memory is off unless `memory_enabled` or the series says `on`.

Invariants:

- A unit never sees the facts of a later unit (story order is Bazarr's `(season, episode)`; season 0 sits outside the chain). Facts come from source subtitles, never from translations. A stale translation is flagged, never retranslated automatically.

- An existing target subtitle is never overwritten by an automatic job. Only an explicit retranslation, cue edit or revert replaces it, and each keeps the previous file as a version first.
- Only a missing language is translated: a sibling subtitle already in the target language (any name, `existing_target_subtitle` in `subtitles.py`) or Bazarr no longer listing it in `missing_subtitles` (`target_still_missing`) ends the job with no model call. Real-Bazarr trials run with `GST_ONLY_PATHS` and `GST_MAX_PENDING` set in both containers (see `docs/CONFIGURATION.md`); never point the worker at a whole library without them. The script's embedded copy of these checks must change with `queue.py`.
- Two model layers: the light default model does the work; the strong model (`gst_fallback_model`) only takes hard jobs (`HardJobError`), outages and spent quota. Do not widen what escalates without looking at `model_layers`.
- A broken glossary file stops the job; a translation never silently runs without it.
- Gemini, TMDB, and Bazarr API keys are never logged or returned unmasked.
- Every HTTP request (static and `/api/*`) passes a session cookie or Basic Auth in `ConsoleHandler.parse_request` (`gst_worker/web.py`) before routing (`gst_worker/auth.py`); the only exceptions are `GET /login` and `POST /api/auth/login`. The single account lives in the SQLite `auth` table as a scrypt hash, seeded from `CONSOLE_USER`/`CONSOLE_PASSWORD` on the first run (the stored account wins afterwards; `CONSOLE_PASSWORD_RESET=1` overwrites it). Passwords and session tokens are never logged, stored in config, or exposed through `RuntimeContext` or `/api/settings`; `CONSOLE_AUTH=off` is the only opt-out.
- Bazarr and the worker must see identical media paths (`/media/...`).
