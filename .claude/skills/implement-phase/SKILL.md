---
name: implement-phase
description: Implement a roadmap phase (P0–P16, including P0b–P0d) or a single FR/NFR from docs/design/SPEC.md and docs/design/ARCHITECTURE.md. Use when the user says "vào P1", "làm FR-3.3", "implement phase", or asks to build any feature of the redesigned translator (subcore engine, providers, glossary, memory, scheduler, SQLite store, console pages, Basic Auth). Unit tests (Python and web) are part of the phase, written with the code, never a follow-up request; the browser test hand-off is the separate `test-plan` skill.
---

# Implement a design phase

The design docs are the contract. `docs/design/` wins over upstream docs; `docs/ARCHITECTURE.md` + `CONTEXT.md` describe the worker as built so far.

## 1. Scope it from the docs

1. Read `PLAN.md` and find the phase: its checklist, its **verification column** ("Kết quả kiểm chứng"), and unchecked items left over from earlier phases.
2. For every FR/NFR/ADR the phase touches, read the exact text in `SPEC.md` / `ARCHITECTURE.md`. Note items marked **[Đổi]** — they deliberately differ from the original SRS; do not "fix" them back.
3. Map the phase's acceptance criteria to SPEC §6 numbers. Those become tests.
4. If the docs are silent or contradictory on something that changes behavior, ask the user (one concise question) instead of guessing; record the answer with the `design-docs` skill.

## 2. Respect the boundaries

- `packages/subcore` = pure engine: no Bazarr, no queue, no HTTP, no filesystem layout of the worker. The worker imports subcore; subcore never imports the worker.
- Provider errors become typed exceptions (`RateLimited`, `QuotaExhausted`, `Unavailable`, `BadOutput`) inside the adapter. Nothing above the provider layer parses error strings.
- Worker seams from AGENTS.md still hold: `RuntimeContext` for wiring, `queue_policy.py` pure, HTTP reaches the queue only via `ConsoleActions`.
- The queue is the `jobs` table (`JobStore`, `JobQueue`, `queue_policy.py` pure); a new kind of job goes in the lists of readers that say which kinds they mean, with a test (AGENTS.md, "Long work is a job"). There is no queue file format and no Bazarr script any more (P10).
- Docs that follow the code are checked by `scripts/doccheck.py` (run by `ci-check`): a new `subserver` module is named in `docs/ARCHITECTURE.md` as `<name>.py`, a new `subcore` module as `subcore.<module>`, a new `/api/*` route appears in `docs/API.md`.
- The console is the React app in `apps/web` (ADR-17): no CDN, touch-first, hash routes, every button that spends model calls goes through the price box; see AGENTS.md "Frontend". The old `apps/server/static/` no longer exists.

## 3. Tests first, never the network

- Every phase ships its unit tests in the same change; a phase without them is not finished, and the user never has to ask.
- stdlib `unittest` only for Python. New subcore tests go in `packages/subcore/tests/`, worker tests in `apps/server/tests/`. A screen or a web lib gets `vitest` tests next to it in `apps/web/src` (`*.test.ts(x)`).
- LLM calls go through the `fake` provider (NFR-8). A test that would hit Gemini/TMDB/Bazarr is a bug.
- Write the acceptance test for the phase (e.g. SPEC §6.4 "missing 3 cues → exactly one repair call with those 3 cues") before the implementation, and assert on prompts/call counts, not just outputs.
- Mandatory invariants to keep covered: never overwrite a non-system subtitle; API keys/passwords never logged or returned; timecodes/indices never sent to the LLM; atomic writes.

## 4. Finish

1. Run the `ci-check` skill; all steps must pass. When `apps/web` changed, also run from `apps/web` (upper-case drive letter on Windows): `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`: what the CI `web` job runs.
2. Update docs in the same change: `docs/API.md` for endpoints, `docs/CONFIGURATION.md` for env/settings, `CONTEXT.md` for new domain terms, `AGENTS.md` if commands or layout changed. Tick the finished items in `PLAN.md` and update the phase status in its overview table; only mark a phase `[x]` after CI on GitHub is green. Add a short "Đã làm ở Px" note in `docs/design/ARCHITECTURE.md` only when the implementation fixed a design choice the doc left open.
3. Add a `CHANGELOG.md` entry under Unreleased.
4. Browser test: when the change touches a screen or what a job does, end the report by offering the `test-plan` skill (a plan for the light session). When the user says "viết test" or "test case" afterwards, that is what they mean; the unit tests were already part of step 3.
5. Report: what was built, which acceptance criteria are now covered by which tests, what is deferred. Distinguish repo changes from image builds and running-service state (AGENTS.md) — a phase being done in code does not mean it is deployed.
