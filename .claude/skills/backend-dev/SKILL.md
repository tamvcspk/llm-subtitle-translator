---
name: backend-dev
description: Backend (apps/server, packages/subcore) change rules for this repo - which layer a new route, service, Store method, job kind, setting or module goes in, how provider errors and answers are typed, and which tests must change with it. Use when adding or changing server code, an endpoint, a job kind, a Store table or a setting, or when the user says "backend", "thêm route", "thêm job", "thêm bảng", "thêm setting".
---

# Backend change

The layers, seams and invariants are in `docs/ARCHITECTURE.md` (sections 2, 10, 11); read the ones your change touches
before editing. This skill says **where** things go and **what ships with them**. Run `ci-check` at the end.

## 1. Where does it go?

| You add | It goes in | Plus |
|---|---|---|
| a pure decision, a type that crosses layers | `subserver/domain/` (imports `subcore` and the standard library only) | a unit test; a port or named result type goes in `domain/ports.py` |
| a use case a route or job calls | `subserver/services/<name>.py`, wired in `RuntimeContext` (`runtime.py`) and built in `worker.py` | never import an adapter, `api`, a socket or an HTTP client |
| code that runs a kind of job | `subserver/jobs/` | see "A new job kind" |
| a thing that touches infrastructure (Bazarr, TMDB, a provider, files the worker does not own) | `subserver/adapters/` | reach it from core only through a port in `domain/ports.py` |
| a `Store` method | the mixin of the table it reads or writes in `adapters/store/<group>.py`, never `__init__.py` | returns a named type from `ports.py`, never `dict` or `sqlite3.Row`; a table or column is a new migration in `store/migrations.py` |
| a route | a row in a page router's regex table (`api/*_api.py`) or a function in `api/console_api.py`; the route only parses, calls a service and formats | see "A new route" |
| a setting | `domain/config.py` (default, validation), read through `SettingsService`; `docs/CONFIGURATION.md`; the screen in `apps/web/src/lib/settings.ts` | live without restart; process-level config is environment only (`SYSTEM_SETTING_KEYS`) |
| engine code (prompt, parse, validate, providers) | `packages/subcore/src/subcore/` | it must not know Bazarr, the queue, HTTP or the database |

A module that fits no layer fails `tests/test_core_boundary.py`. Name it in `docs/ARCHITECTURE.md` (`<name>.py`, or
`subcore.<module>`); `scripts/doccheck.py` fails until you do.

## 2. A new route

1. A request body gets a model in `api/schemas.py` (unknown fields pass; a flag that makes a call spend is `StrictBool`;
   a body no model names is `RequestObject` and the use case validates it).
2. The answer gets a model in `api/answers/<page>.py` (`Answer` forbids unnamed fields, `OpenAnswer` ignores them,
   `maybe()` marks a field that may be left out, a field's meaning goes in the docstring under it, a query parameter in
   the router's `QUERIES` / `query(...)`), declared on the route (`response_model=` or the `ANSWERS` table).
3. Synchronous work goes through `common.blocking`; never block the event loop. A `ValueError` from a use case becomes a 400.
4. A line in `docs/API.md`; a case in `tests/test_answer_contract.py` (its last test fails for a route with no model or no
   case); regenerate `docs/openapi.json` with `uv run --locked --directory apps/server python -m subserver.api.openapi`
   and `npm run gen:api` in `apps/web`, and commit both (CI fails on a diff; never edit them by hand).
5. A route that spends model calls does not run them: it queues a job, and `estimate_only` answers the price with no spend.

## 3. A new job kind (every kind that spends model calls is one)

Read ARCHITECTURE section 4 first. Then: the `kind` in `TRANSLATING_KINDS` or `READING_KINDS`; a handler registered with
`register_handler` in `worker.py` (like `ExtractJob`, `SuggestJob`, `JudgeJob` in `read_jobs.py`); a unique key so a second
request does not stack; providers from `provider_guard.JobProviders.for_task(...)` and errors through
`provider_guard.mapped_errors`; every reader that says which kinds it means (`job_name`, `latest_for(kind=)`, `layer_report`,
`catalog_view`, `_hard_job`, `estimate_retry`) lists it; the model of the task from `config.model_for_task`. A test for each
of those. There is no second background mechanism: a wait that spends no call wakes the existing loop.

## 4. Errors, secrets, telemetry

- Provider failures are typed (`QuotaExhausted`, `RateLimited`/`Unavailable`, `AuthError`, `ModelNotFound`, `BadOutput`) in
  the adapter; they become the queue's classes only in `provider_guard.mapped_errors`. Never classify by error text.
- Anything written down (log, event, prompt body, error file) passes `redaction.py`; a new secret kind is added there. Keys
  are read through `SettingsService`, never `os.environ`. The API returns only the last four characters of a key.
- Telemetry is a best-effort port: never inside a job's transaction, never raises, a `reason` a person can read.
- A stale translation is flagged, never retranslated automatically; an automatic job never overwrites a target.

## 5. Tests that ship with the change (stdlib `unittest`, never the network)

- A use case: a test on `Store(":memory:")` (real migrations, no fake DB) with `subcore.providers.fake` for the model; assert
  on prompts and call counts, not only outputs.
- A pure function: a plain unit test. A new `Store` method: a test next to the table's other tests.
- Every new route: the `test_answer_contract.py` case; serve the app with `tests/web_harness.py` (`serve_console`) or
  `starlette.testclient.TestClient`.
- Run one module from `apps/server`: `uv run --locked --directory apps/server python -m unittest tests.<module>`.
- Then the whole pipeline with `ci-check`. Docs that follow the code: `docs/ARCHITECTURE.md`, `docs/API.md`,
  `docs/CONFIGURATION.md`, `CONTEXT.md` for a new domain term, `CHANGELOG.md` under Unreleased.
