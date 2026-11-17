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
| `subserver/bazarr.py`, `subserver/tmdb.py` | `apps/server/subserver/…` |
| `subserver/queue*.py` | `apps/server/subserver/…` |
| `bazarr-postprocess/gst_enqueue.sh` | same |
| `worker.py`, `tests/` | `apps/server/…` (upstream's `static/` console has no counterpart: the console is `apps/web`, so skip such changes) |

```bash
git log --oneline --no-merges main..upstream-sync -- subserver/bazarr.py subserver/tmdb.py 'subserver/queue*.py' bazarr-postprocess/
```

Skip: changes to the gst/Gemini translation path that P2 replaces (`translation.py`, gst CLI flags) unless they fix a live bug, Chinese-default UX tweaks, upstream deployment details (their host, image name, Dependabot assignee).

Show the user the candidate list with a one-line verdict each (take / skip + reason) before cherry-picking anything non-trivial.

## 2. Cherry-pick

```bash
git switch -c sync/upstream-$(date +%Y-%m) main
git cherry-pick -x <sha>
```

Git's rename detection usually maps `subserver/x.py` → `apps/server/subserver/x.py`. If it does not (conflict "deleted in HEAD"), apply with a path prefix instead:

```bash
git format-patch -1 <sha> --stdout | git apply --3way --directory=apps/server --include='subserver/*' --include='worker.py' --include='tests/*'
git format-patch -1 <sha> --stdout | git apply --3way --include='bazarr-postprocess/*'
```

then commit with `(cherry picked from commit <sha>)` in the message. Test files that read repo-level files use `REPO_ROOT`/`APP_ROOT` (see `apps/server/tests/test_frontend_structure.py`); fix upstream `ROOT` paths accordingly.

If a fix touches code the fork has already rewritten, port the idea by hand and say so in the commit message instead of forcing the patch.

## 3. Verify and report

Run the `ci-check` skill. Report: commits taken (sha → new sha), commits skipped with reason, any hand-ported changes. Do not push or open a PR unless the user asks.
