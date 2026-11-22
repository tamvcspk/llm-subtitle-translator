---
name: architect
description: Architecture decisions for this repo - when a change needs an ADR, how package and layer boundaries are kept, how docs/ARCHITECTURE.md (as built) and docs/design/ARCHITECTURE.md (ADRs and target) split the work, and running scripts/doccheck.py. Use when a change crosses layers or packages, adds a dependency, moves modules, proposes a new seam, or when the user says "kiến trúc", "ADR", "ranh giới", "tách module", "doccheck".
---

# Architect

Two documents, two jobs (P15f):

| File | Holds | Language | Changes when |
|---|---|---|---|
| `docs/ARCHITECTURE.md` | the system **as built**: layers, module reference, flows, Seams, Invariants | English | code changes (same diff) |
| `docs/design/ARCHITECTURE.md` | the **ADRs** (why), and design not built yet (P16 research) | Vietnamese | a decision is made or changed; use `design-docs` |

`AGENTS.md` keeps general rules, commands and links only; Seams and Invariants live in `docs/ARCHITECTURE.md` and are not
copied elsewhere. Requirements and acceptance criteria are `docs/design/SPEC.md`; the order of work is `PLAN.md`.

## When a change needs an ADR

Write one (next number in `docs/design/ARCHITECTURE.md` section 1, via `design-docs`) when it: adds a dependency or a
process, moves a layer boundary or adds a seam, changes where state is stored, changes what the console may call, or reverses
an existing ADR (mark the old one's text, do not delete it). A change that only follows an ADR needs no new one; add a short
"Đã làm ở Px" note to that ADR only when the implementation settled something the ADR left open.

Format of an ADR: the decision, the options with a verdict each (the rejected ones stay, with their reason), what it costs,
what does not change (name the ADRs it leaves alone), the PLAN phases that do it.

## Boundaries (checked by `tests/test_core_boundary.py`)

- `subcore` knows no Bazarr, queue, HTTP or worker database; the worker imports it, never the reverse (ADR-2).
- `subserver`: `domain` imports `subcore` and the standard library; `services` and `jobs` import `domain`, each other, the
  `Store` and the `Telemetry` sink, never an adapter, `api`, a socket or an HTTP client; `adapters` never import a use case or
  `api`; `api` never imports the `Store`, `JobQueue` or `JobStore`. `worker.py` stays the composition root and imports no
  socket, SQLite or HTTP server.
- The application's own SQLite is not behind a port (ADR-12); ports are only for what the project does not own.
- One container, one process, one port (ADR-4). The final image never holds Node (ADR-17).
- A new module goes in exactly one layer; a new workspace member needs its `pyproject.toml` copied in the `Dockerfile`.

## Steps

1. Read `docs/ARCHITECTURE.md` sections 2, 10, 11 and the ADRs the change touches (grep the topic in
   `docs/design/ARCHITECTURE.md`). Say which seam or invariant is affected.
2. Prefer the smallest change that keeps the boundaries; a move of files is its own commit with no logic change.
3. If a boundary must move, change the rule in `tests/test_core_boundary.py` in the same diff and write the ADR first.
4. Update `docs/ARCHITECTURE.md` in the same diff as the code (a new module named as `<name>.py`, a new seam or invariant in
   section 10/11). Run `python scripts/doccheck.py`: it fails for a `subserver` or `subcore` module not named there, an
   `/api/*` route not in `docs/API.md`, and a skill or `AGENTS.md` that points at a file that does not exist.
5. Update `PLAN.md` (tick items, status table) and `CHANGELOG.md`. Report what changed in the design and what did not.
