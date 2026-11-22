# Architecture

> Describes the worker as built. The repository is a uv workspace:
> worker paths below (`worker.py`, `subserver/`, `tests/`) live under
> `apps/server/`; the console is `apps/web` and the new engine package is `packages/subcore/`.
> Module names below are file names inside the layer packages of `subserver/` (see "Layers of `subserver/`").
> The image keeps the old runtime layout (`/app/worker.py`, `/app/subserver`) and serves the
> console build from `/app/static`.
> Target design: [design/ARCHITECTURE.md](design/ARCHITECTURE.md).
>
> Kept in step with the code by `scripts/doccheck.py` (run it with `ci-check`): every module of `subserver/` is named
> here as `<name>.py`, every module of `packages/subcore` as `subcore.<module>`, and every `/api/*` route is in
> [API.md](API.md). That is a floor, not a design: the reasons for the seams are in `AGENTS.md`, the order of the work
> in `PLAN.md`. P15f replaces this file with one document written for the structure ADR-18 gives the code.

## Components

### Bazarr: the worker pulls

Since P10 Bazarr pushes nothing (the post-processing script and the folder shared with it are gone). The
worker reads the library through the `MediaLibrary` port (`BazarrMedia.catalog()`): series (paged), each
series' episodes, movies (paged), with their subtitles and the languages Bazarr lists as missing. `CatalogSync`
(`catalog.py`) writes the answer to `catalog_items`, and decides first whether it may (an error, a timeout, an
empty answer or one that shrank more than `catalog_shrink_guard` is set aside and the cache stays). A read
never creates a job. Jobs come from the admin's requests (`job_requests.py`, behind `POST /api/jobs`) and from
the `auto_request` policy (`auto_request.py`), which runs after an accepted read.

### Worker

`worker.py` is the runtime entrypoint and composition root: it builds the adapters
(`Store`, `JobStore`, `BazarrMedia`, `TmdbMetadata`, the HTTP client), passes them
down as ports, and holds `process_job` and `QueueWorker`. It imports no socket,
SQLite or HTTP-server module itself (`tests/test_core_boundary.py`). In normal mode it starts:

- a background worker loop that consumes one job at a time (`--no-worker` leaves it out, `--no-queue` keeps the
  loop from claiming);
- the catalog sync loop (`run_catalog_sync`, the only caller of `sync_and_request`) and the backup and clean-up
  scheduler;
- a FastAPI app, served by uvicorn on the one port, for the console and the JSON API (`subserver/api/web.py`,
  ADR-18, P15d). The three loops above are `BackgroundLoop`s (`runtime.py`) that `main()` hands to
  `start_web_server`; the app starts them on daemon threads in its `lifespan` (with `--worker-only`, which has no
  console, `main()` starts them itself). There is no `BackgroundTasks` and no other background mechanism (D7).

### Layers of `subserver/` (ADR-18, P15c)

`subserver/` is five layer packages and the composition seam; every module belongs to exactly one layer, and the
direction of imports is checked by `tests/test_core_boundary.py`:

| Package | Holds | May import |
|---|---|---|
| `domain/` | pure decisions and the port types: `ports`, `config`, `queue_policy`, `rules`, `gather`, `suggestion_keys`, `subtitles`, `translation`, `redaction` | `subcore` and the standard library |
| `services/` | the use cases: `units`, `console`, `library`, `versions`, `memory`, `suggestions`, `judge`, `metrics`, `cleanup`, `catalog*`, `job_requests`, `auto_request`, `glossaries`, `styles`, `style_service`, `proposals`, `feedback`, `estimates`, `bootstrap`, `auth` | `domain`, `jobs`, the `Store` and the `Telemetry` sink |
| `jobs/` | the queue and what runs a job: `jobs` (`JobStore`), `queue` (`JobQueue`), `provider_guard`, `read_jobs`, `translation_attempt`, `intake` | the same as `services/` |
| `adapters/` | what touches infrastructure: `bazarr`, `tmdb`, `http`, `gemini`, `llm`, `backups`, `connection_tests`, `settings`, `logs`, `bodies`, `telemetry` and the `store/` package | `domain` and other adapters |
| `api/` | the FastAPI app `web` (`create_app`, the pages, the lifespan), the gate `security`, the shared pieces `common`, the routes of the account `auth_api` and of the rest `console_api`, and the page routers `*_api` (`library`, `style`, `suggestion`, `catalog`, `insight`, `keys`) | everything below, but never the `Store`, `JobQueue` or `JobStore` directly: a route asks a service |
| `runtime.py` | `RuntimeContext` (the typed composition seam) and `BackgroundLoop` (with `worker.py`, the composition root) | any layer |

Where a new file goes: a pure function or a type that crosses layers is `domain/`; a class a route or a job calls to do
something is `services/`; code that runs a kind of job is `jobs/`; a module that opens a socket, a file the worker does
not own or a model provider is `adapters/`; a route is `api/`. A module that fits none of these fails the boundary test.

The `Store` is a package, `adapters/store/`: `base.py` holds the connection, the lock and the migrations runner,
`migrations.py` the schema scripts, and one repository mixin per group of tables (`auth.py`, `bundles.py`,
`settings.py`, `records.py` (calls, events, signals, prompt bodies), `insights.py`, `versions.py`, `library.py`
(series, units, corrections), `glossary.py`, `suggestions.py`, `catalog.py`, `memory.py`) is assembled into the one
`Store` the rest of the worker holds (`store/__init__.py`). A method lives in the file of the table it touches.

Domain code is split under `subserver/`:

- `config.py`: the settings schema (defaults, validation, the system keys the console
  cannot write, the four key fields), language normalization, and Bazarr language
  list loading;
- `subtitles.py`: subtitle path mapping and local source subtitle scanning;
- `queue.py`: queue admission, lifecycle, skip checks, and snapshots. `JobQueue`
  owns recovery, settling, claiming, deferred retries, quota pauses, and status
  persistence. Console retry and delete operations share its transition helpers;
- `queue_policy.py`: pure retry and daily quota lifecycle decisions. This is the
  policy seam; filesystem moves remain in `queue.py`.
- `catalog.py`: `CatalogSync` (the guarded read of Bazarr into `catalog_items`) and `CatalogPaths` (a job
  finds its files again by Bazarr's id after a rename, FR-14.3);
- `catalog_view.py`: `CatalogView`, the Library read model. The label of each (item, language) is derived on
  reading from the catalog, the version history, the jobs and the decisions (FR-14.5); there is no state column;
- `job_requests.py`: `JobRequests`, the planning, estimate and creation of requests (`POST /api/jobs`);
  `auto_request.py`: `AutoRequest`, the policy; `catalog_actions.py` / `catalog_api.py`: the Library page's
  use cases and their pure router;
- `console.py`: `ConsoleActions`, the queue use cases used by the HTTP adapter (retry, cancel, delete, stop, the
  snapshot, the job's spec and the layer report). Jobs are not made here (`job_requests.py` and `auto_request.py`
  make them); it keeps request routing separate from queue orchestration;
- `jobs.py`: `JobStore`, the `jobs` table: every state change of a job is a method here (claim, wait, finish,
  stop, retry, delete of a finished row); `queue.py` decides, this stores;
- `runtime.py`: `RuntimeContext`, the typed composition seam of the running process (see below);
- `ports.py`: the ports (`MediaLibrary`, `Metadata`, `ProviderFactory`, the `Telemetry` sink) and the named types the
  `Store` returns (`tests/test_core_boundary.py` keeps a `dict` or `sqlite3.Row` from leaking out of it);
- `auth.py`: `ConsoleAuth`, the single console account (a scrypt hash in the `auth` table, sessions, Basic Auth),
  checked by `AuthGate` (`security.py`) before any route;
- `estimates.py`: `Estimator`, what an action that spends model calls would cost, said before it runs (the price box);
- `gather.py`: gathers one `TranslationBundle` from the episode's rules, story memory and metadata (the worker's half
  of the bundle contract; pure);
- `provider_guard.py`: what every job that calls the model shares: the time limit, the call budget
  (`JobProviders`), and `mapped_errors`, the one place a provider's typed failure becomes the queue's class;
- `read_jobs.py`: the handlers of the jobs that read and do not translate (`ExtractJob`, `SuggestJob`, `JudgeJob`);
- `intake.py`: the one-time settling of an older queue (folders and the P8 rows) at start-up;
- `bootstrap.py`: the first episodes `auto_request` is allowed to ask for while a series has nothing approved;
- `connection_tests.py` and `gemini.py`: the Settings "Test" buttons (a typed key against Bazarr, TMDB or the
  provider) and the lists of models the Settings pickers show;
- `web.py`: the console's HTTP adapter: `create_app` builds the FastAPI app from the runtime context (the gate, every
  router, the sign-in page and the static files as the last route, the `lifespan` that starts the loops), and
  `start_web_server` runs it with uvicorn (no uvicorn log configuration and no access log: the worker's own redacting
  log formatter is the only one, and the gate logs a failed request). A route parses, calls a use case and formats;
  `worker.py` imports `create_app` and `start_web_server`;
- `security.py`: `AuthGate`, a pure ASGI middleware in front of every path (routes, the fallback and the static
  files): a session cookie or Basic Auth, the login lockout, the same-origin check on cookie writes, a re-issued
  session cookie on the way out; the only open requests are `GET /login` and `POST /api/auth/login`;
- `common.py`: what routes share: the JSON answer and the `{"error": ...}` shape, `ConsoleRoute` (a `ValueError` is
  a 400, anything else a logged 500), `blocking` (synchronous work runs on the thread pool, never on the event loop)
  and `add_table_routes`, which gives each row of a pure router's regex table a real FastAPI route (so `app.routes`
  is the list of routes) and serves it with that router's own `handle`;
- `schemas.py`: the pydantic models of the request bodies (P15d, step 2). Every model lets unknown fields through and
  names only what the API layer itself reads; a flag that makes a call spend is `StrictBool`; a body no model names is
  `RequestObject`, any JSON object that the use case validates in its own words. A body that does not fit is a `400`
  `{"error": "field: reason"}`; the body is read as JSON whatever its `Content-Type`;
- `answers/` (P15e): the pydantic model of what each route **answers**, one module a page: `answers/common.py`
  (the confirmation, the price of a request, the count of what a change would flag stale, the error), `answers/console.py`
  (account, status, queue, settings, keys, models, logs, events, backups, clean-up), `answers/catalog.py`,
  `answers/library.py` (units, cues, corrections, versions and the comparison, story memory, glossaries),
  `answers/style.py`, `answers/suggestions.py` and `answers/insights.py`. A model is a contract and is not run: a route
  keeps sending what its use case returned, and `response_model` (or the `ANSWERS` table of a pure router) only puts the
  model in the OpenAPI document. `Answer` forbids a field it does not name, `OpenAnswer` ignores one (a job is a stored
  payload); `maybe()` marks a field the answer may leave out. `tests/test_answer_contract.py` is what keeps a model true:
  it starts the real worker on seeded data (`tests/contract_kit.py`), calls every route and checks each answer strictly
  against the model of its route;
- `openapi.py`: exports the OpenAPI document from the routes alone (no server, no database) to `docs/openapi.json`, which
  is committed; `apps/web` generates `src/api/schema.gen.ts` from it. `tests/test_openapi.py` fails when the file is not
  what the code says, the `web` job when the generated types are not what the file says;
- `auth_api.py` and `console_api.py`: the routes that are not a page's table: the account (`/api/auth/*`), and status,
  queue buttons, settings, languages, models, logs, events, backups, clean-up and the connection test;
- `settings.py`: `SettingsService` (an adapter), the console settings and the API
  keys in SQLite (P6, FR-20): one-time import of `config.json`, one-time seeding of
  the keys from the environment, precedence stored value > environment > default,
  the runtime settings mapping (`settings()`), the masked public view, key
  management and tests, subscribers told of every change, and the warnings Settings
  shows (a refused key, a model the provider does not know);
- `keys_api.py`: the router of `/api/keys` over `SettingsService` (like `library_api` for the library pages: a regex
  table and a pure `handle`, plus `register`, which puts the table on the app);
- `translation_attempt.py`: `TranslationAttempt`, the execution seam for one
  translation job: it reads the source, calls `subcore` in-process, maps typed
  provider errors to the queue's exceptions, and publishes the result;
- `translation.py`: the failure classes the queue acts on
  (`ProviderUnavailableError`, `DailyQuotaExceededError`, `HardJobError`,
  `ContextGapError`) and `redact`, which delegates to `redaction.py`;
- `redaction.py`: the one secret filter (known values plus secret shapes) used by
  the log formatter, events, prompt bodies and error text;
- `telemetry.py`: `Telemetry`, the bounded queue and single writer thread behind
  events, quality signals and call records (see "Logging and measurement");
  `events_page` answers `GET /api/events`;
- `bodies.py`: `BodyLog`, the rotating JSONL files that keep the prompt and answer
  of each model call;
- `llm.py`: builds the provider for an attempt: one rate-limited provider per API key,
  with failover to the next key. The adapter follows the setting `llm_provider`:
  `GeminiProvider`, or `subcore`'s `OpenAICompatProvider` at `llm_base_url` (which may
  run without a key). The model of each task comes from `config.model_for_task`
  (translate, extract, summarize, research; each falls back to `gst_model`). `LiveProviderFactory` (the factory of the
  running worker) takes the usable keys from `SettingsService` at every build, so a
  new key is used by the next call, and wraps each key in `KeyedProvider`, which
  reports a refused key (`AuthError`: the key becomes `invalid`), a use, and an
  unknown model (`ModelNotFound`: a Settings warning) back to the service;
  `build_provider` is the plain function over a settings mapping;
- `store/` (`store/base.py`, `store/migrations.py`, `store/auth.py`, `store/bundles.py`, `store/settings.py`,
  `store/records.py`, `store/insights.py`, `store/versions.py`, `store/library.py`, `store/glossary.py`,
  `store/suggestions.py`, `store/catalog.py`, `store/memory.py`): SQLite (`app.db`, versioned migrations, file
  mode `0600`) with the `translations`, `llm_calls`, library, `facts`, `summaries_cache`, `context_snapshots`,
  `events`, `quality_signals`, `llm_bodies`, `settings` and `api_keys` tables. `Store` is the facade the worker
  holds; tests build it on `Store(":memory:")`;
- `memory.py`: `StoryMemory`, the worker side of story memory: story order,
  prerequisite fact extraction, gap waiting, the context bundle and its staleness;
- `versions.py`: `TranslationVersions`, publishing with version history, backup
  before overwrite, and revert;
- `cleanup.py`: `Cleanup`, the file clean-up use case (FR-13): `run(dry_run)` returns a
  `CleanupReport` per group (finished `jobs` rows, version copies, earlier glossary states,
  leftover `.*.tmp`), and `run_if_due` is the daily run called from the backup scheduler
  thread (its last run is a `_cleanup_last_run` marker in `settings`). It deletes nothing
  itself except temporary files: rows go through `JobStore.delete_expired` (which refuses
  any state that is not finished), copies through `TranslationVersions.prune` (only
  inside `backups/subtitles/`, marking the version `pruned`), glossary history through
  `Glossaries.prune_history`. The temporary-file sweep skips every media root and the
  folder of every target subtitle, and only lists what it finds there;
- `glossaries.py`, `styles.py`, `style_service.py`, `style_api.py` (P12): the glossary of a
  series or movie in `app.db` (with its history and the one-time import of the old YAML
  files), the genre presets, series/episode styles and character cards, and the use cases
  and routes of the Style page, including the trial of a style (writes nothing). The style
  layers are laid on each other by `subcore.style`; `rules.py` hashes them;
- `tmdb.py`: TMDB lookup and translation description generation;
- `bazarr.py`: Bazarr API refresh, wanted-item lookup, and API-key parsing;
- `backups.py`: server-side backup listing, creation and restore. The configuration
  comes from `SettingsService` and holds no API key unless a manual backup asks for
  them; a restore never touches the console account;
- `logs.py`: logging setup, UTC formatting with secrets removed from every line
  (`RedactingFormatter`), a log level that can change while running, 5 MiB rotation
  (three backups), bounded snapshots, and clearing the current log;
- `http.py`: small HTTP client and JSON file cache helpers.

The worker translates in-process with the `subcore` engine (workspace package
`packages/subcore`), writes the target subtitle, and asks Bazarr to scan the
affected series or movie. There is no subprocess and no `gst` CLI.

`QueueWorker` binds `JobQueue.process_once` to the configured translation
workflow through an execution callback and exposes `ConsoleActions` to the HTTP
adapter. Queue lifecycle tests use the same
interface with a callback and a real temporary directory, without HTTP or TMDB
setup. Recovery is explicit at worker startup, so creating a queue handle does
not recover work that is still running.

Translation progress is reported by the attempt through the queue's status
callback and stored in the processing job (`stage`, `progress_done`,
`progress_total`); queue snapshots just read the job file. A failed or
interrupted attempt starts again from the beginning: only a finished result is
kept, so there are no work files to resume from.

The SQLite store (`DATA_DIR/app.db`, default `STATE_DIR`) holds machine-owned
state. `translations` keeps every version published for a target path with its
sha256, input hash, and a copy under `DATA_DIR/backups/subtitles/`; `llm_calls`
records every model attempt (tokens, latency, outcome). The queue is the `jobs` table
(see Queue Layout below).

A target file is *system-made* when the store's current version for its path has
the file's sha256. An automatic job never overwrites any existing target. A
retranslate or revert first keeps a file that is not the current version (a hand
edit, or a file someone else put there) as an `external` version, so nothing is
lost, then writes atomically. Reverting restores a stored version byte for byte
and records the restore as a new version.

### Library, corrections and retranslation (P3)

A *unit* (an episode or a movie) is registered the first time a job for it runs
(`library.py`; identity from Bazarr ids, then the file name, with best-effort
enrichment from the Bazarr API). `units` and `series` rows, a
`unit_outputs(unit, language) → path` mapping, `corrections`, and a `unit_id` plus
`glossary_hash` on every translation version live in `app.db` (schema 2; schema 3
adds story memory, below).

`rules.py` merges the series glossary (stored in `app.db` since P12 and read by
`glossaries.py`; before P12 it was `DATA_DIR/series/<key>/glossary.<lang>.yaml`) with the
unit's active episode corrections and the style layers for one
episode. Terms and address rules carry `from`/`until` windows. The result goes to
the prompt, and its hash is stored as the version's `glossary_hash`: when the hash
computed now differs, the translation is *stale* (flagged only). Hand-edited cues
(`cue_edit`) are outside the hash; they are locked output, and so are the few-shot
examples made from them. The glossary's `target_lang` is outside it too (a version is
already keyed by its language, and the store writes that field into a glossary on its
first save). `Library.impact_units` is the one place that counts how many translations a
change to the rules would make stale; every save, restore, copy, adoption or deletion that
changes a hash asks it first and answers with `stale_now`, `stale_after`, `would_stale`
(`dry_run` only counts). A glossary change counts one language; a style, a card, a preset
or an episode's style counts every language. `UnitRules.for_window` numbers rules built
on a whole source for a part of it (the trial of a style).

`units.py` (`UnitService`) implements the console actions: cue view, corrections,
retranslation, versions, diff, revert, glossary read/write. `library_api.py` routes
`/api/library/*`, `/api/units/*` and `/api/series/*` to it. A retranslation is a
queue job (`trigger: retranslate`, `priority: 100`, `retranslate.range`,
`retranslate.correction_ids`); the attempt asks `subcore` for the range only
(`only=`), passing locked cues and the current translation of cues outside the
range as `keep=`, which are copied verbatim and shown to the model as reference
translations.

### Story memory (P4)

`subcore.memory` is the pure part: `facts.py` reads the *facts* of one unit from its
source subtitle (characters, relationship changes with the cue where they show, terms,
a summary; structured output, nothing but the numbered text is sent), `fold.py` folds the
facts of the units before N into the *story state @N* and renders the prompt sections
("Story so far", "Relationship state", "This episode"), `compress.py` condenses old
summaries with one model call. No queue, no Bazarr, no filesystem layout.

`memory.py` (`StoryMemory`) is the worker's side. Story order is `(season, episode)` as
Bazarr numbers them; season 0 is outside the chain and sees the state after
`series.specials_after` (default: the end of the first season). Before a unit is
translated, `plan()`:

1. asks Bazarr for the series' episodes and their source subtitles (`list_series_episodes`,
   best effort) and registers them as units;
2. for each earlier unit, reads its facts if its source exists and none are stored for
   that sha256 + `EXTRACT_PROMPT_VERSION` (`facts` table), keeping stored facts when the
   source has since gone; a unit with neither a source nor facts is a *gap*;
3. while there is a gap and `memory_gap_wait_seconds` has not passed, raises
   `ContextGapError`; the queue defers the job (`deferred_reason: context-gap`, `gap_since`
   in the job file, rechecked every 5 minutes). After the wait, or for a retranslation, it
   translates with `context_complete = 0` and the missing labels stored;
4. builds the bundle; if the story exceeds `memory_budget_tokens`, the older summaries
   are condensed by one `summarize` call cached in `summaries_cache`.

Prerequisites run inline in the same attempt (one job at a time, ADR-4), not as separate
`extract` queue jobs. The bundle's *fingerprint* (a hash of the label and content hash of
every earlier unit's facts plus the unit's own) is stored as `translations.memory_hash`
and the rendered text in `context_snapshots`. `Library.target_info` marks a version
`stale` when the fingerprint computed from the stored facts now differs, or when a unit
listed in its `missing_units_json` has a source subtitle now; both facts-per-unit lookups
read "the newest stored facts for the current extraction prompt version", so building a
bundle and checking staleness can never disagree. `TranslationAttempt` takes the extra
sections from the plan (after the glossary and the episode corrections) and maps provider
errors raised while extracting exactly as for translating, so a spent daily quota pauses
the queue and the facts already read are kept.

### Logging and measurement (P5)

The worker leaves three kinds of trace, all in `app.db`, plus the prompt bodies in files
(FR-19, ADR-15). Core code reaches them through the `Telemetry` port (`event`, `signal`,
`llm_call`); `NULL` (`telemetry.NULL`) is the default wherever it is optional, and
`worker.py` builds the real one over the `Store` and a `BodyLog`.

- **Events** (`events`): a decision with its reason: `enqueue.skipped`, `job.created`,
  `catalog.synced`, `catalog.kept`, `request.created`, `policy.requested`, `policy.capped`, `job.claimed`, `model.selected`, `job.escalated`, `job.deferred`,
  `job.requeued`, `job.skipped`, `job.failed`, `job.done`, `quota.paused`,
  `translation.cached`, `translation.finished`, `version.published`, `version.reverted`,
  `version.cue_edit`, `settings.changed`, `worker.started`. `GET /api/events` reads them
  (filter by job, unit, kind or kind family). Each is also written to the log as one JSON
  line (`EVENT {...}`), which is why the log level also decides which events are kept.
- **Call records** (`llm_calls`): one row per provider attempt with the prompt version,
  glossary hash, kind (translate, repair or split), batch, HTTP status, finish reason,
  attempt number, cues repaired and an estimated cost. `subcore`'s `CallLog` carries what
  the provider layer knows; the worker adds the hashes.
- **Quality signals** (`quality_signals`): `cue_edit` (before and after), `revert`,
  `retranslate`, `validate` (one per problem the validator found, repaired or not), `ab` (the admin's pick between
  a variant and the current text) and `judge` (the LLM judge's opinion, never counted as `ab`).
- **Bodies**: `DATA_DIR/logs/llm/<day>.<n>.jsonl`, gzipped when closed; `llm_bodies`
  indexes `(call, segment, offset, length)` with offsets into the uncompressed stream, and
  keeps the row (marked `rotated`) after cleanup removes the file.

`Telemetry` never raises and never waits: records go into a bounded in-memory queue read by
one writer thread, outside any job transaction. A full queue or a failed write drops the
record, counts it (`dropped`, shown by `/api/events`) and logs a warning now and then
(NFR-11). The same thread prunes events past `events_retention_days` and body files past
`log_retention_days` and `log_max_mb` once an hour and whenever a segment fills. Every text
passes the shared redactor first.

`RuntimeContext` is the composition seam for the running process. It carries the
queue console actions, Bazarr integration, backup maintenance, and HTTP transport
adapters alongside the worker's shared settings and state paths. The legacy
module-level functions remain compatibility entry points for direct callers and
older tests; new runtime composition should depend on the typed context fields.

The direct seam tests in `tests/test_architecture_modules.py` verify policy and
console actions, and `tests/test_translation_attempt.py` runs the translation
attempt against fake providers, independently of the HTTP server and the
complete queue lifecycle tests.

Bazarr is only read: `tests/test_catalog.py` runs the catalog, the labels, the requests and the policy against a
fake `MediaLibrary`, and `tests/test_media_library_contract.py` checks `BazarrMedia.catalog()` (paging, failures)
against a fake Bazarr HTTP.

### Jobs that read (F7)

Every task that spends model calls is a row of `jobs` and goes through the one queue, so the daily quota,
`max_calls_per_day`, the breaker, Stop and a restart apply to all of it. `kind` says what a job does: `publish` and
`variant` translate a subtitle (`TRANSLATING_KINDS`); `extract` reads the facts of one episode (key `extract:<unit>`);
`suggest` looks for proposals for one series and language (`suggest:<series>:<lang>`, at most three calls); `judge`
asks the LLM judge about one candidate (`judge:<unit>:<lang>:<version>`, calls of 30 cues). `READING_KINDS` have no
subtitle, no bundle and no strong model. `QueueWorker.run_job` runs a job by its kind through the handlers `worker.py`
registers (`translate_job`, and `ExtractJob`, `SuggestJob`, `JudgeJob` in `read_jobs.py`). A handler does its work and
lets typed provider errors through `provider_guard.mapped_errors`; it takes its providers from
`provider_guard.JobProviders` (deadline, `max_calls_per_job`, `max_calls_per_day`, one provider per model). The admin's
request only queues; `estimate_only` stays synchronous because it spends nothing. Every reader of jobs says which kinds
it means, and a new kind is added to those lists with a test.

### Suggestions and the Review inbox (P13, P14)

One table (`suggestions`), one use case. `SuggestionService` (`suggestions.py`, routes in `suggestion_api.py`) is the only
place that stores a generator's proposals (`propose`), decides them (`accept`, `reject`, `undo`, `bulk`) and applies them;
a generator hands it a list of proposals and has no review logic of its own. Accepting goes through
`Glossaries.save_doc` (and the character card when a character carries its voice, in one transaction) and counts the
translations it would flag stale with `Library.impact_units`; it never retranslates. `suggestion_keys.py` is the pure
*identity* of a suggestion (kind, name or pair, `from`; case and spacing folded): a decided suggestion is kept and the
same entry is never proposed again. Generators: `proposals.py` (`FactsProposer`) draws on story facts through
`subcore.suggest` and reaches the model through a `suggest` job; `feedback.py` (`FeedbackProposer`, over
`subcore.feedback`) turns a swap of words repeated in corrected cues into a term with no model call; research grounding
(P16) will be a third. A proposal with no research `sources` is never applied by `auto_accept_terms`.

### Insights, the golden set and the judge (P15)

`metrics.py` (`Metrics`) is the one place that works out the figures of FR-19.5: it rolls `llm_calls`,
`quality_signals` and the `translation.finished` events into `metrics_daily` (one row per UTC day, name and labels)
and the report only adds those rows, so a figure outlives its events; a rate with nothing to divide by is `null`.
`insight_api.py` serves `GET /api/metrics` and a series' golden set (`subcore.golden`, scored by
`subcore compare --golden`). `judge.py` (`JudgeService`, over `subcore.judge`) queues, runs and reads the optional LLM
judge (`judge_enabled`, off by default): a verdict is advice bound to the exact pair of texts it saw, never an `ab`
signal, never a cue edit.

### The engine: `subcore`

`packages/subcore` is the translation engine, pure of Bazarr, the queue, HTTP and the worker's database
(`tests/test_core_boundary.py` and the package's own tests keep it so). Its modules:

- `subcore.srt`: parse and serialise SRT; a cue keeps its original index and timing line, so output reproduces the
  timecodes byte for byte. The model never sees an index or a timecode;
- `subcore.bundle`: `TranslationBundle`, everything one translation reads, as plain data (the worker builds it in
  `gather.py`, the CLI loads it with `--bundle`; the same bundle gives the same prompt);
- `subcore.glossary`: the glossary model (terms, characters, address rules with `from`/`until` windows), its YAML
  exchange format, merging, and the check of locked terms; `subcore.style`: the style layers and `resolve_style`;
  `subcore.matching`: the one place that decides whether a line mentions a name; `subcore.fewshot`: examples chosen from
  corrected cues;
- `subcore.translate.planner` (how many parts a unit is translated in), `subcore.translate.prompt` (the system and
  request text), `subcore.translate.engine` (`run`, `render_requests`, `compose_request`: batches, repair of missing
  cues, splitting, the review pass), `subcore.translate.validate` (parse and check a reply),
  `subcore.translate.alignment` (reply text attached to the wrong cue number), `subcore.translate.review` (a finished
  translation against its source: the `validate` command and the worker share it);
- `subcore.providers.base` (the `Provider` interface and the typed errors), `subcore.providers.gemini`,
  `subcore.providers.openai_compat` (standard library only), `subcore.providers.fake` (deterministic, for tests and dry
  runs) and `subcore.providers.limiter` (`RateLimiter`, `ResilientProvider`: RPM/TPM/RPD windows, retry with backoff,
  key failover);
- `subcore.memory.facts`, `subcore.memory.fold` and `subcore.memory.compress` (see Story memory), `subcore.suggest`
  (candidates from facts, then one call per kind), `subcore.feedback` (repeated corrections to a term),
  `subcore.golden`, `subcore.compare` (numbers for two configurations or one against a golden set) and `subcore.judge`
  (rubric, request, parse; pure);
- `subcore.cli`: the declarative `subcore` command (`translate`, `bundle`, `render-prompt`, `validate`, `compare`);
  it knows no database, queue or HTTP.

### Web Console

The console is the React app in `apps/web` (TypeScript, Vite, Mantine, React Router in hash mode, TanStack Query; ADR-17).
It calls the local worker API only; it does not talk directly to Bazarr, Gemini, or TMDB. The Dockerfile builds it in a
Node stage and copies only `dist/` into the final image as `/app/static`, which the worker serves with a fallback to
`index.html` (routes are `#hash`, so there is none on the server). The sign-in page `login.html` is the one file outside
the bundle (`apps/web/public/`): it is self-contained and the only static file served without a session.

The screens, their data and their layout are in `docs/design/UX.md`; the code is organised as `api/` (typed calls and
queries, one `types.ts` kept equal to `docs/API.md`), `lib/` (pure rules, tested without a DOM), `components/` (shared
pieces: the price box, confirmation sheets, the style form) and `pages/`. A page never builds HTML from text: React
escapes it, and paths and provider errors are untrusted.

## Queue Layout

Since P8 jobs are rows of the `jobs` table in `app.db` (ADR-11), claimed by `JobQueue`
(`queue.py`) through `JobStore` (`jobs.py`). States: `queued`, `running`, `waiting`
(with `wait_reason`: `quota`, `provider`, `context`, `settle`, `rate`),
`done`, `skipped`, `failed`, `cancelled`. Provider health lives in `provider_health`
(one row per provider and model, `*` for a refused key), decided by the pure
`queue_policy.py`.

The old folders `pending/ processing/ deferred/ done/ failed/ held/ rejected/` are only read once, at
start-up (`intake.import_queue_tree`), and moved to `queue.migrated/`: `processing`/`deferred` become jobs,
`done`/`failed` the history, a `held` job is dropped (the item is Missing again) and a `rejected` one
becomes an Ignored decision (`unit_decisions`). `pending` is what the old Bazarr script dropped and nobody approved,
so it is not run either: those items are Missing again and the start-up logs how many (an admin's own request or a
retranslation still waiting there is a job). The `held`/`rejected` rows P8 may have left in the table are
settled the same way. A "no" is written down before the row or the folder that held it goes; one that cannot be
written yet (no library, a job that cannot be placed) stays, with its folder, and is tried again at the next
start-up. Running it again changes nothing.

## Job Shape

The fields of a job, as they were in the queue files; since P8 they are the `jobs` row (`jobs.py` has the columns,
`kind` says what the job does, see "Jobs that read") and the old files only matter to `intake.py`.

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
model after a provider outage or a hard job (`escalated_from` and
`escalation_reason` then say why). A finished job also records its `result`
(`translated`, `skipped`, `skipped-not-wanted`, ...). A manual retry clears the
retry and escalation fields. An
optional `trigger` (`auto` by default, or `retranslate`) lets an explicit
retranslation overwrite an existing target after backing it up.

## Translation Flow

1. The worker picks the oldest pending job.
2. A job is made by an admin's request or by the `auto_request` policy, never by a read of Bazarr. It is
   dropped, with no model call, unless its language is still missing
   (automatic jobs only; see the version rules above): the exact target file or
   any sibling subtitle in that language exists, the folder is outside
   `GST_ONLY_PATHS`, or Bazarr no longer lists the language in the item's
   `missing_subtitles` (`skipped-not-wanted`; if Bazarr cannot answer, the files
   decide). The same checks run when the job is requested, so most never enter the
   queue.
3. TMDB context is built when `TMDB_API_KEY` is available.
4. If the store already holds a result for the same source sha256, target
   language, and context hash (model, temperature, prompt version, TMDB
   description), the stored copy is published and no model call is made.
5. Otherwise `subcore` translates with the model the queue chose for this
   attempt: the default `gst_model` (a light model), or the strong model
   `gst_fallback_model` when the job was handed over (hard job or outage) or the
   default model's daily quota is exhausted. Every call goes
   through a rate limiter (RPM/TPM/RPD) and retries `429`/`503` with
   exponential backoff, jitter, and the server's `retry-after`. Calls time out
   after 300 seconds and a job has a 30 minute watchdog (`JOB_TIMEOUT_SECONDS`).
6. Provider failures arrive as typed exceptions, not parsed text. A `503` or
   rate limit that outlasts the in-process retries moves the job to `deferred`
   for 5, 15, 30, then 60 minutes. While a job waits out an outage, no other job
   starts, so a queue of jobs does not spend daily quota on the same errors.
   After four delayed retries the job moves to the fallback model once; without
   a fallback, or when the fallback is also unavailable, it moves to `failed`.
   A rejected API key (`AuthError`; the key is marked `invalid` and passed over, and
   the next call uses another key if there is one) or an exceeded watchdog fails the
   job immediately; until P8 moves such a job to `waiting`, the admin retries it
   after pasting a new key. An unknown model (`ModelNotFound`) also fails the job and
   raises a warning on Settings. Output
   that stays invalid after repair and splitting (`HardJobError`) is a hard job:
   the default model is a light one, so the job moves once to the strong model
   (`gst_fallback_model`), recording `escalated_from`; if that also fails, or
   there is no strong model, the job fails. `layer_report` (`/api/status`
   `model_layers`) counts how many jobs escalate and how many the strong model
   finishes, to decide whether the layering is worth keeping.
7. A daily quota error pauses that model for 24 hours. If another configured
   model still has quota, the job returns to `pending` and continues on it.
   Once every configured model is exhausted, the job and waiting jobs move to
   `failed`, automatic retries are cancelled, and admission and manual retries
   are blocked until the first model's pause expires, or until the settings
   name a model whose quota is not exhausted.
8. On success the result is written atomically, recorded as a version, and the
   job moves to `done`.
9. The worker refreshes Bazarr with `scan-disk`; if item IDs are missing, it
   falls back to full subtitle scan tasks.

## Path Model

Bazarr and the worker must agree on media paths.
The recommended Docker model is:

```yaml
volumes:
  - /host/media:/media
```

If Bazarr sees a subtitle as `/media/Movie/Movie.en.srt`, this worker must be
able to read and write that exact path inside its container.
