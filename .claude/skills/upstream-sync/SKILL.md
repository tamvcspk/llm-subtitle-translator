---
name: upstream-sync
description: Review new commits in the upstream repo kingzleshe/gemini-srt-translator-bazarr and cherry-pick relevant Bazarr/queue fixes into this fork, remapping paths to the uv workspace layout. Use for the monthly upstream sync (ADR-2), or when the user asks "sync upstream", "lấy fix từ upstream", "cherry-pick upstream".
---

# Upstream sync

Policy (AGENTS.md, ADR-2): cherry-pick only, never merge, never push to `upstream` (its push URL is `no_push`; keep it that way).

## 1. Fetch and list candidates

```bash
git fetch upstream
git branch -f upstream-sync upstream/main          # tracking branch, never committed to
git log --oneline --no-merges main..upstream-sync
```

Focus on files the fork still shares with upstream (upstream path → fork path):

| Upstream | Fork |
|---|---|
| `gst_worker/bazarr.py`, `gst_worker/tmdb.py` | `apps/worker/gst_worker/…` |
| `gst_worker/queue*.py` | `apps/worker/gst_worker/…` |
| `bazarr-postprocess/gst_enqueue.sh` | same |
| `worker.py`, `static/`, `tests/` | `apps/worker/…` |

```bash
git log --oneline --no-merges main..upstream-sync -- gst_worker/bazarr.py gst_worker/tmdb.py 'gst_worker/queue*.py' bazarr-postprocess/
```

Skip: changes to the gst/Gemini translation path that P2 replaces (`translation.py`, gst CLI flags) unless they fix a live bug, Chinese-default UX tweaks, upstream deployment details (their host, image name, Dependabot assignee).

Show the user the candidate list with a one-line verdict each (take / skip + reason) before cherry-picking anything non-trivial.

## 2. Cherry-pick

```bash
git switch -c sync/upstream-$(date +%Y-%m) main
git cherry-pick -x <sha>
```

Git's rename detection usually maps `gst_worker/x.py` → `apps/worker/gst_worker/x.py`. If it does not (conflict "deleted in HEAD"), apply with a path prefix instead:

```bash
git format-patch -1 <sha> --stdout | git apply --3way --directory=apps/worker --include='gst_worker/*' --include='worker.py' --include='static/*' --include='tests/*'
git format-patch -1 <sha> --stdout | git apply --3way --include='bazarr-postprocess/*'
```

then commit with `(cherry picked from commit <sha>)` in the message. Test files that read repo-level files use `REPO_ROOT`/`APP_ROOT` (see `apps/worker/tests/test_frontend_structure.py`); fix upstream `ROOT` paths accordingly.

If a fix touches code the fork has already rewritten, port the idea by hand and say so in the commit message instead of forcing the patch.

## 3. Verify and report

Run the `ci-check` skill. Report: commits taken (sha → new sha), commits skipped with reason, any hand-ported changes. Do not push or open a PR unless the user asks.
