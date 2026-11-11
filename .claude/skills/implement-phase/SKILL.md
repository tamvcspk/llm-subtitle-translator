---
name: implement-phase
description: Implement a roadmap phase (P0, P0b, P1…P5) or a single FR/NFR from docs/design/SPEC.md and docs/design/ARCHITECTURE.md. Use when the user says "vào P1", "làm FR-3.3", "implement phase", or asks to build any feature of the redesigned translator (subcore engine, providers, glossary, memory, scheduler, SQLite store, console pages, Basic Auth).
---

# Implement a design phase

The design docs are the contract. `docs/design/` wins over upstream docs; `docs/ARCHITECTURE.md` + `CONTEXT.md` describe the inherited worker you are changing.

## 1. Scope it from the docs

1. Read `PLAN.md` and find the phase: its checklist, its **verification column** ("Kết quả kiểm chứng"), and unchecked items left over from earlier phases.
2. For every FR/NFR/ADR the phase touches, read the exact text in `SPEC.md` / `ARCHITECTURE.md`. Note items marked **[Đổi]** — they deliberately differ from the original SRS; do not "fix" them back.
3. Map the phase's acceptance criteria to SPEC §6 numbers. Those become tests.
4. If the docs are silent or contradictory on something that changes behavior, ask the user (one concise question) instead of guessing; record the answer with the `design-docs` skill.

## 2. Respect the boundaries

- `packages/subcore` = pure engine: no Bazarr, no queue, no HTTP, no filesystem layout of the worker. The worker imports subcore; subcore never imports the worker.
- Provider errors become typed exceptions (`RateLimited`, `QuotaExhausted`, `Unavailable`, `BadOutput`) inside the adapter. Nothing above the provider layer parses error strings.
- Worker seams from AGENTS.md still hold: `RuntimeContext` for wiring, `queue_policy.py` pure, HTTP reaches the queue only via `ConsoleActions`.
- Queue file format changes must update `bazarr-postprocess/gst_enqueue.sh` (embedded Python payload) **and** its tests in the same change.
- Console stays dependency-free vanilla ES modules, no CDN, mobile-usable (ADR-7).

## 3. Tests first, never the network

- stdlib `unittest` only. New subcore tests go in `packages/subcore/tests/`, worker tests in `apps/worker/tests/`.
- LLM calls go through the `fake` provider (NFR-8). A test that would hit Gemini/TMDB/Bazarr is a bug.
- Write the acceptance test for the phase (e.g. SPEC §6.4 "missing 3 cues → exactly one repair call with those 3 cues") before the implementation, and assert on prompts/call counts, not just outputs.
- Mandatory invariants to keep covered: never overwrite a non-system subtitle; API keys/passwords never logged or returned; timecodes/indices never sent to the LLM; atomic writes.

## 4. Finish

1. Run the `ci-check` skill; all steps must pass.
2. Update docs in the same change: `docs/API.md` for endpoints, `docs/CONFIGURATION.md` for env/settings, `CONTEXT.md` for new domain terms, `AGENTS.md` if commands or layout changed. Tick the finished items in `PLAN.md` and update the phase status in its overview table; only mark a phase `[x]` after CI on GitHub is green. Add a short "Đã làm ở Px" note in `docs/design/ARCHITECTURE.md` only when the implementation fixed a design choice the doc left open.
3. Add a `CHANGELOG.md` entry under Unreleased.
4. Report: what was built, which acceptance criteria are now covered by which tests, what is deferred. Distinguish repo changes from image builds and running-service state (AGENTS.md) — a phase being done in code does not mean it is deployed.
