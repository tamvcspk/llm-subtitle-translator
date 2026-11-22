# Development

## Local Setup

Use [uv](https://docs.astral.sh/uv/) for local dependency management. The
checked-in lockfile selects Python 3.14 for parity with the Docker image; the
application supports Python 3.12 through 3.14.

```bash
uv sync --locked --all-packages
```

`uv` downloads the requested Python version when it is not already installed.

The repository is a uv workspace with a virtual root (`pyproject.toml` has no
project of its own) and one `uv.lock`:

| Member | Path | Notes |
|---|---|---|
| `llm-subtitle-server` | `apps/server/` | `worker.py`, `subserver/`, `tests/`. Not packaged (`package = false`). The console it serves is `apps/web` (its own `package.json`, not a uv member). |
| `subcore` | `packages/subcore/` | Translation engine library (`src/` layout), installed editable. |

Add a dependency to one member with `uv add --package <member> <dep>`.

## Run Tests

Each member's tests run in their own `unittest` process (both test folders are
named `tests`):

```bash
uv run --locked python -m unittest discover -s apps/server/tests -t apps/server -v
uv run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
uv run --locked python -m compileall -q -f apps/server/worker.py apps/server/subserver packages/subcore/src
```

The console has its own checks, run from `apps/web` (Node 24, see `.nvmrc`):
`npm ci && npm run typecheck && npm run lint && npm test && npm run build`.

The types of the answers come from the server. Changing a model in `apps/server/subserver/api/answers` (or a request body
in `api/schemas.py`) is two generated files, committed with the change:

```bash
uv run --locked --directory apps/server python -m subserver.api.openapi   # docs/openapi.json (no server needed)
npm run gen:api                                                            # in apps/web: src/api/schema.gen.ts
```

`tests/test_openapi.py` and the `--check` step of CI fail when the first is out of date, the `web` job when the second is.
`tests/test_answer_contract.py` is the test of a model: it starts the real worker on seeded data
(`tests/contract_kit.py`, `tests/preview_seed.py`), calls every route and checks each answer strictly against its model.
A new route needs its model and a case there, or the last test of that file fails.

`python scripts/doccheck.py` (standard library only) fails when a `subserver` or
`subcore` module is missing from [ARCHITECTURE.md](ARCHITECTURE.md) or an `/api/*`
route is missing from [API.md](API.md). The server test suite runs it too
(`tests/test_doccheck.py`), so a new module or route needs its line in the docs
in the same change.

## Judging Translation Quality

The `subcore` command works without the worker, the database or the queue (it
needs only a key in `GEMINI_API_KEY`, or `--provider fake` for a dry run):

```bash
uv run subcore translate file.srt --glossary g.yaml   # translate and write the result
uv run subcore bundle ...                              # write the bundle of a translation
uv run subcore render-prompt ...                       # print the requests, no model call
uv run subcore validate ...                            # review a translation against its source
uv run subcore compare file.srt --bundle a.json --bundle b.json --golden gold.json   # two bundles against a golden set
```

The cues an admin corrects form a **golden set** per series and language
(`GET /api/series/{id}/golden?lang=vi`); measure a change of prompt or style
against it before adopting it. The procedure is in [QUALITY.md](QUALITY.md). The
optional LLM judge (`judge_enabled`, off by default) only advises on the Compare
page; it is never a score of its own.

## Trying a Change in the Browser

`scripts/preview.ps1` (Windows) seeds an isolated `local-state/preview`, builds
`apps/web` when it is stale and serves the console on `127.0.0.1:6790` without
consuming queue jobs; `-Live` uses a real Gemini key from
`local-state/secrets/gemini.key` with hard caps on calls. See `AGENTS.md`
("Development Commands") for the flags and for how a browser test is handed over
(`test-plan` and `web-test` skills). How the code is laid out, and which rules a
change must keep, is [ARCHITECTURE.md](ARCHITECTURE.md); the skills by role
(`backend-dev`, `frontend-dev`, `designer`, `architect`) say where a new piece goes.

## Dependencies

The translation engine is the workspace package `packages/subcore`; its only
network dependency is `google-genai`. `uv.lock` pins exact versions for local
development, CI, and Docker. Dependabot opens a daily pull request when a
compatible `google-genai` release is available; pull requests must pass the CI
workflow before merging, and the existing Docker workflow publishes the updated
image after the change reaches `main`.

## Run Locally

Create `.env`, then run:

```bash
uv run python apps/server/worker.py --host 127.0.0.1 --port 6789
```

The worker expects the media and Bazarr config paths from your environment to be
valid; set `STATIC_DIR=apps/web/dist` (after `npm ci && npm run build` in `apps/web`) when running outside the image;
for work on the console itself run `npm run dev` in `apps/web`, which proxies `/api` and `/login` to the worker. For
isolated UI work, use `--no-worker` so it does not consume queue jobs, or use
`scripts/start-local.ps1 [-Demo] [-NoWorker]` on Windows (it needs the build):

```bash
uv run python apps/server/worker.py --no-worker --host 127.0.0.1 --port 6789
```

## Coding Notes

- Keep the Bazarr integration read-only (the catalog read) and behind the
  `MediaLibrary` port; a read never creates a job.
- Keep model calls inside `subcore` providers, reached through
  `subserver/jobs/translation_attempt.py` and the job handlers; do not install
  dependencies into the Bazarr image.
- Never log Gemini, TMDB, or Bazarr API keys.
- Preserve the no-overwrite rule for existing target subtitle files.
- Prefer focused unittest coverage around path handling, queue transitions,
  command construction, and Bazarr refresh behavior.

## Release Checklist

1. Update `CHANGELOG.md`.
2. Run the test commands above.
3. Build the image:

   ```bash
   docker compose build
   ```

4. Check the example config still renders:

   ```bash
   docker compose --env-file .env.example config
   ```

5. Tag the release.
6. Push `main` or a `v*.*.*` tag and verify the `Docker image` GitHub Actions
   workflow published the GHCR image.
