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
| `llm-subtitle-worker` | `apps/worker/` | `worker.py`, `gst_worker/`, `static/`, `tests/`. Not packaged (`package = false`). |
| `subcore` | `packages/subcore/` | Translation engine library (`src/` layout), installed editable. |

Add a dependency to one member with `uv add --package <member> <dep>`.

## Run Tests

Each member's tests run in their own `unittest` process (both test folders are
named `tests`):

```bash
uv run --locked python -m unittest discover -s apps/worker/tests -t apps/worker -v
uv run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
uv run --locked python -m compileall -q -f apps/worker/worker.py apps/worker/gst_worker packages/subcore/src
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
uv run python apps/worker/worker.py --host 127.0.0.1 --port 6789
```

The worker expects the media and Bazarr config paths from your environment to be
valid; set `STATIC_DIR=apps/worker/static` when running outside the image. For
isolated UI work, use `--no-worker` so it does not consume queue jobs, or use
`scripts/start-local.ps1 [-Demo] [-NoWorker]` on Windows:

```bash
uv run python apps/worker/worker.py --no-worker --host 127.0.0.1 --port 6789
```

## Coding Notes

- Keep Bazarr integration in the post-processing script fast and side-effect
  small.
- Keep Gemini calls in the worker app (`gst_worker/translation.py`); do not
  install dependencies into the Bazarr image. (`apps/worker/gst_worker/translation.py`
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
