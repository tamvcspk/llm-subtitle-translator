---
name: ci-check
description: Run this repo's full CI pipeline locally (uv workspace sync, worker + subcore unittest suites, compileall, subcore CLI check) with the same uv version as GitHub Actions, and report pass/fail per step. Use before committing, after moving files or touching pyproject/uv.lock/Dockerfile, or when the user asks to "chạy test", "check CI", "giữ CI xanh".
---

# CI check

Mirror `.github/workflows/ci.yml` exactly. If the workflow changed, follow the workflow, not this file, and update this file.

## Steps

Run from the repo root. Use the uv version pinned in `ci.yml` (`setup-uv` → `version:`); the local `uv` may be older and can rewrite `uv.lock` differently.

```bash
UV_VER=$(sed -n 's/.*version: "\([0-9.]*\)".*/\1/p' .github/workflows/ci.yml | head -1)
U="uvx --from uv==$UV_VER uv"
$U sync --locked --no-dev --all-packages
$U run --locked python -m unittest discover -s apps/worker/tests -t apps/worker -v
$U run --locked python -m unittest discover -s packages/subcore/tests -t packages/subcore -v
$U run --locked python -m compileall -q -f apps/worker/worker.py apps/worker/gst_worker packages/subcore/src
$U run --locked subcore --help
```

Each workspace member's tests run in a **separate** process: both folders are named `tests`, so one combined `discover` would collide.

## When something fails

- `--locked` fails ("lockfile needs to be updated"): a `pyproject.toml` changed. Run `$U lock` (same uv version), then check `git diff uv.lock` shows **no version bumps** you did not intend. Never `--upgrade` as a side effect.
- New workspace member: add it to root `pyproject.toml` `[tool.uv.workspace].members`, copy its `pyproject.toml` in the `Dockerfile` before `uv sync`, add its test/compile steps to `ci.yml` and to this file.
- Docker install step (cannot build images locally — see AGENTS.md): simulate it by copying only root `pyproject.toml`, `uv.lock`, every member `pyproject.toml`, and `packages/subcore/README.md` + `packages/subcore/src` (the worker depends on subcore) into a scratch dir and running `$U sync --locked --no-dev --package llm-subtitle-worker` there.

## Report

One line per step: passed / failed (with the failing test names and the assertion). State test counts (e.g. "worker 125 OK, subcore 1 OK"). Do not claim CI on GitHub is green unless you checked the actual run (`gh run list --workflow ci.yml -L 1`).
