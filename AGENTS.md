# Project Collaboration Rules

General rules, commands and links. How the code is built (layers, seams, invariants) is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); it is not repeated here. Work by role has a skill (see "Skills and agents").

## Fork Status

This repository is `tamvcspk/llm-subtitle-translator`, a fork of `kingzleshe/gemini-srt-translator-bazarr` (remote `upstream`, push disabled). The fork is being redesigned; the target design lives in `docs/design/SPEC.md` and `docs/design/ARCHITECTURE.md` (ADRs and what is not built yet) and takes precedence over upstream docs when they conflict. `docs/ARCHITECTURE.md` describes the code as built.

- Pull upstream fixes by cherry-pick only; never push to `upstream`.

## Runtime Environment

- Deployment target for this fork is not set up yet. The upstream author's host details (`192.168.1.1`, SSH as root) do not apply to this fork; do not connect to them.
- Default container paths: `/state`, `/media`, web port `6789`. `/queue` is only read once at start-up to settle an older queue (P10); there is no `/bazarr-postprocess` any more.

## Build and Deployment

- Images are built through GitHub. The default scope is to modify repository code, run relevant tests, and report results; do not build images locally or on the remote device.
- Use read-only SSH checks when the live service state is needed. A code change request does not authorize deployment. Modify container files, replace remote scripts, or restart or recreate containers only when the user explicitly requests an update to the running service.
- Deploy using images built through GitHub. If the user explicitly requests a temporary direct container modification, back up the affected files first and explain that a normal restart preserves the changes, while recreating the container from an image overwrites changes made inside the container.
- Clearly distinguish repository code changes, completed GitHub image builds, and running container updates in reports. Claim only states that have been verified.

## Hard rules (the full list is "Invariants" in docs/ARCHITECTURE.md)

- Never overwrite an existing target subtitle in an automatic job; only an explicit retranslation, cue edit or revert replaces it, and each keeps the previous file as a version first.
- API keys, passwords and session tokens are never logged or returned unmasked; every text sink passes `redaction.py`.
- `packages/subcore` knows no Bazarr, queue or HTTP (ADR-2); `subserver/` layers import only downward (`tests/test_core_boundary.py`).
- Every task that spends model calls is a job in the one queue, and every button that spends calls goes through an Estimate/Confirm dialog.
- Unit tests ship with the change, never as a separate request. Tests never call the network or a real model (`fake` provider).
- Bazarr and the worker must see identical media paths (`/media/...`). Real-Bazarr trials run with `GST_ONLY_PATHS` set.

## Development Commands

The repo is a **uv workspace** (virtual root, one `uv.lock`): `apps/server` (`llm-subtitle-server`: `worker.py`, `subserver/`, `tests/`, not packaged) and `packages/subcore` (`subcore` engine library, `src/` layout). Install with `uv sync --locked --all-packages`; add deps with `uv add --package <member> <dep>`. Tests use stdlib `unittest`, not pytest; each member's tests run in a separate process (`tests/test_answer_contract.py` also starts `worker.py` as a process on seeded data, about 25 s). CI (`.github/workflows/ci.yml`) runs:

```bash
uv run --locked python -m unittest discover -s apps/server/tests -t apps/server -v
uv run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
uv run --locked python -m compileall -q -f apps/server/worker.py apps/server/subserver packages/subcore/src
uv run --locked subcore --help
uv run --locked --directory apps/server python -m subserver.api.openapi --check
```

Run one module or test by dotted name (from the member directory):

```bash
uv run --locked --directory apps/server python -m unittest tests.test_worker_queue
uv run --locked --directory apps/server python -m unittest tests.test_worker_queue.TestWorkerQueueTests.test_enqueue_translation_jobs_for_enabled_targets
```

The Docker image installs only `llm-subtitle-server` (which depends on `subcore`, so `packages/subcore/src` and its README are copied too) and keeps the old runtime layout (`/app/worker.py`, `/app/subserver`, `/app/static`). Every new workspace member needs its `pyproject.toml` copied in the `Dockerfile` before `uv sync --locked`.

Docs that follow the code: `python scripts/doccheck.py` (also run by the `ci-check` skill and by `test_doccheck.py`) fails when a `subserver` module, a `subcore` module or an `/api/*` route is not named in `docs/ARCHITECTURE.md` or `docs/API.md`, or when a skill or `AGENTS.md` points at a file that is not there.

Local console preview on Windows: `scripts/start-local.ps1 [-Demo] [-NoWorker]` runs against an isolated `local-state/` directory instead of the Docker `/state` and `/queue` mounts. `-NoWorker` keeps the process from consuming queue jobs.

Trying a change in the browser with real rows and no key: `scripts/preview.ps1 [-Reset] [-Rebuild]` seeds `local-state/preview` (`apps/server/tests/preview_seed.py`, a fake provider, no Bazarr), builds `apps/web` when it is stale and serves the console on `127.0.0.1:6790` with `--no-worker`. `node scripts/smoke/smoke.mjs [--routes ...] [--steps file.mjs]` walks it in Edge through `playwright-core` (installed on first use into the git-ignored `local-state/tools`, not a project dependency) and fails on a blank page, a console error or a failed `/api` call; `scripts/smoke/steps/` holds the clicks of one change. Testing is split over two sessions through one file, `local-state/handoff/TEST-PLAN.md` (git-ignored): the heavy session writes a short plain-language plan after a change (`test-plan` skill, "viết kịch bản test") and later reads the findings ("đọc kết quả test"); a light session drives the real app in a browser tool when the user says "test" (`web-test` skill), writes only the Findings section and never edits code. `scripts/preview.ps1 -Bazarr` (not with `-Live`) adds a stand-in Bazarr (`scripts/fake_bazarr.py` on `127.0.0.1:6791`; `/__fake/shrink` and `/__fake/restore` in a browser tab make it answer with a much smaller library or the full one), runs the catalog sync loop and leaves an older worker's `queue/` folders for the start-up to settle (`preview_seed.py --old-queue`); the worker runs with `--no-queue`, so nothing can call a model. `scripts/preview.ps1 -Live` runs the real worker with a real Gemini key (put it alone in `local-state/secrets/gemini.key`, git-ignored, or set `GEMINI_API_KEY`; `-MaxCallsPerDay` is a hard cap, default 1000 (free tier: Flash 10K a day, Flash Lite 150K), and `-MaxCallsPerJob` one per job, default 20; every task runs on `-LightModel` (default `gemini-3.1-flash-lite`) and only the fallback layer and the judge on `-StrongModel` (default `gemini-3.8-flash`; pick models with free-tier quota on the key's project, `gemini-3.5-flash` has none); `suggest_from_facts` is off; these are read on the first run of a state, so use `-Reset`; no episode is pre-translated; no Bazarr; `GST_ONLY_PATHS` confines it to the demo media); the plan's "Money" section lists the only actions allowed to call the model. The tester drives the console through the project's Playwright MCP server (`.mcp.json`, `@playwright/mcp` pinned, Edge, headed so the user can watch, 820x1180, screenshots and snapshots in `local-state/pw/`; approved for the project in `.claude/settings.json`) with full freedom on the disposable preview data: events, screenshots, console, network, resizing. `smoke.mjs --steps` (Playwright modules in `scripts/smoke/steps/`) is optional and no longer part of the plan. Without `-Live` nothing that calls the model can be tried, and Bazarr never can.

`subcore` has a declarative CLI for judging translation quality without the worker (it knows no DB, queue or HTTP): `uv run subcore translate file.srt --glossary g.yaml` (key from `GEMINI_API_KEY` (`OPENAI_COMPAT_API_KEY` for `openai_compat`; a key never crosses providers), or `--api-key-file`; `--provider fake` for a dry run), plus `bundle` (write the bundle of a translation), `render-prompt` (print the requests, no call), `validate` (review a translation against its source) and `compare` (two bundles, or one against a golden set). Every translating command works from a bundle (`--bundle`); flags win over it.

Console (`apps/web`; Node 24, see `.nvmrc`; the rules are in the `frontend-dev` skill):

```bash
npm ci                # lockfile only, scripts off (.npmrc)
npm run typecheck && npm run lint && npm test && npm run build   # what the CI `web` job runs
npm run dev           # Vite on :5173, proxies /api and /login to the worker (GST_WORKER_URL, default :6789)
npm run gen:api       # src/api/schema.gen.ts from ../../docs/openapi.json (the CI `web` job runs it and asks for no diff)
```

On Windows run `npm test` from a shell whose path has an upper-case drive letter (`C:/Src/...`); with `c:\...` Vitest loads two copies of itself and every suite fails with "failed to find the current suite". Add dependencies with `npm install --save-exact`.

## Which test is meant

- **Unit tests** (Python `unittest`, web `vitest`) are part of every change and phase: written with the code, in the same diff, never a separate request. A change without them is not done.
- **"viết test", "test case", "kịch bản test"** from the user means the browser hand-off: use the `test-plan` skill (write `local-state/handoff/TEST-PLAN.md`, then the light session runs `/web-test`). It does not mean "write more unit tests".
- **"test" / "test web"** typed in the light session is the `web-test` skill; "đọc kết quả test" in the heavy session is `test-plan` part B.

## Where things are

- `docs/ARCHITECTURE.md`: layers, module reference, job and request flow, subsystems, **Seams** and **Invariants**. Read it before changing queue, translation, Bazarr or HTTP behavior.
- `CONTEXT.md`: domain vocabulary (translation job, translation attempt, translation version, daily quota pause, queue lifecycle); use these terms in code and reports.
- `docs/API.md` (endpoints), `docs/openapi.json` (generated), `docs/CONFIGURATION.md` (settings), `docs/design/UX.md` (screens).
- `PLAN.md`: roadmap phases (P0-P16), checklists and status; tick items as work lands. `docs/design/SPEC.md` and `docs/design/ARCHITECTURE.md`: requirements, acceptance criteria, ADRs.

## Skills and agents

Skills in `.claude/skills/`, agents in `.claude/agents/`:

| Role | Use | For |
|---|---|---|
| Phase work | `implement-phase` | a roadmap phase or a single FR/NFR from scope to report |
| Backend | `backend-dev` | where a route, service, `Store` method or job kind goes; `mapped_errors`; tests |
| Frontend | `frontend-dev` | `apps/web`: generated types, `settings.ts`, `draft.ts`, the price box, touch rules |
| Design | `designer` skill and `designer` agent | UX.md and wireframes before code; the agent edits docs, never code |
| Architecture | `architect` | ADRs, package boundaries, doccheck; with `design-docs` for the Vietnamese design docs |
| QA | `qa` agent, `test-plan`, `web-test`, `ci-check` | check a change against SPEC section 6; the browser hand-off; the CI pipeline locally |
| Upstream | `upstream-sync` | cherry-pick of upstream fixes |

## Luật chung của kit (project-kit, Mode 1)

- Chỉ báo "xong" khi `bash scripts/check` thoát mã 0 (gom các lệnh CI ở trên); dán dòng kết quả cuối khi báo cáo.
- Việc ngoài phạm vi spec (`docs/design/SPEC.md`) và PLAN.md không tự làm; ghi vào mục ý tưởng để sau.
- Không làm mục Non-goals trong `docs/design/SPEC.md`.
- Quyết định có đánh đổi ghi vào `docs/DECISIONS.md` (chỉ thêm; ADR chi tiết vẫn ở `docs/design/ARCHITECTURE.md`).
- Trước khi chuyển sang tool khác: ghi đè `docs/HANDOFF.md` (≤30 dòng) và commit.
- Không ghi giá trị trong `.env` vào bất kỳ file nào được commit.
