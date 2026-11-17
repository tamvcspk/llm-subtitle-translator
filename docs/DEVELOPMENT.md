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
- Keep Gemini calls in the worker app (`subserver/translation.py`); do not
  install dependencies into the Bazarr image. (`apps/server/subserver/translation.py`
  until P2 replaces it with `subcore`.)
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
