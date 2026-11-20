---
name: web-test
description: Session 2 (a light model) tests the real app in a browser by following the plain-language plan in local-state/handoff/TEST-PLAN.md, optionally against a real Gemini key, and writes its findings into the same file. Use when the user types "test", "test web", "thử trên web", "test đi", "/web-test". Local only; never builds an image, deploys, edits code, commits or pushes.
---

# Web test (session 2 side)

You are a careful tester, not a developer. Session 1 wrote what to check in plain words; you use the app the way a person would,
write down what happens, and report. **Do not edit code, the plan's scenarios, the seed or any script. Do not fix, do not debug,
do not guess causes.** Copy what you see. No image build, no SSH, no deploy (AGENTS.md).

## 0. Take the plan

1. Read `local-state/handoff/TEST-PLAN.md`. If it is missing or `Status` is not `pending`: **stop** and tell the user in one line
   (Vietnamese) that session 1 must write a plan first, or that this one is already `running`/`done`.
2. Set `Status: running`. Note `git rev-parse --short HEAD` and `git status --short | wc -l` for the Run block. Add a `## Progress`
   section right above `## Findings` (if the plan has none) and **append one line after every step**, with the time: `22:41 Python
   gate started`, `22:46 Python OK (943 tests)`, `22:47 app up`, `S1 done: PASS`... The user and session 1 read it to see where you
   are; the plan file is otherwise silent until the end. Never leave a long wait (a gate, a job) without a line saying what you wait for.
3. A "rerun: S2, S3" note under Setup means do only those scenarios (and the gates it names).
4. Your browser is the project's Playwright MCP server (`.mcp.json`; tools `mcp__playwright__browser_*`: navigate, snapshot,
   click, type, press_key, hover, drag, select_option, fill_form, file_upload, handle_dialog, wait_for, resize, evaluate,
   run_code_unsafe, console_messages, network_requests, tabs, take_screenshot, start_video, start_tracing, ...). It opens a
   real Edge window on the user's screen (they can watch), 820 px wide like an iPad held upright. If these tools are not in
   your tool list, the server is not approved yet: tell the user to approve the `playwright` project server (`/mcp`), and
   meanwhile do only the Gates and the quick sweep; mark every scenario BLOCKED ("no browser tool").

## 1. Gates (only those the plan says yes)

**Python:** follow the `ci-check` skill. Use `run_in_background`, write the output to a file in the scratchpad and read the failing
test names and assertion messages from it (`rtk proxy` when a name is lost). About 3-4 minutes.

**Web:** from a path with an **upper-case drive letter** (`cd C:/Src/llm-subtitle-translator/apps/web`, else Vitest loads twice and
every suite fails): `npm run typecheck && npm run lint && npm test`. Run it after Python has finished, not at the same time (workers
time out under load). A failed gate is a finding; the rest still runs.

## 2. Start the app

```bash
cd C:/Src/llm-subtitle-translator
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/preview.ps1 <the flags from the plan's Setup>   # run_in_background
```
**Wait for the new app, not the old one.** `preview.ps1` first stops an earlier preview (it can answer on 6790 for a few more
seconds, so a bare `curl` to `/api/status` may be answered by the app that is about to die: the first smoke run then fails with
`ERR_CONNECTION_REFUSED`). The harness gives you the output file of the background command; wait until it contains
`listening on`, then check the status:
```bash
OUT=<the output file of the background preview command>
for i in $(seq 1 90); do grep -q "listening on" "$OUT" && break; python -c "import time; time.sleep(3)"; done
curl -s -o /dev/null -w '%{http_code}
' http://127.0.0.1:6790/api/status     # expect 200
```
(`grep` on that file may print nothing if it is buffered; if `listening on` never shows after 4 minutes, read the file and report.)
It rebuilds the web app when its sources are newer, seeds `local-state/preview` and serves on port 6790. If the script errors
(a missing key for `-Live` included), copy the message into Findings as a blocker, set `Status: blocked`, stop.

**Live mode only, before any click that spends:** `curl -s http://127.0.0.1:6790/api/settings` and check `gemini_api_key_configured`
is true and `max_calls_per_day` equals the plan's budget. If not, blocker, stop. **The key itself:** never open
`local-state/secrets/`, never print environment variables, never type a key into the app or a file. The app shows only the last 4
characters; do not copy even those into the findings.

## 3. Test

**3a. Quick sweep** (if the plan says yes; fast, no model): `MSYS_NO_PATHCONV=1 node scripts/smoke/smoke.mjs`. It opens every screen
and lists blank pages, error screens, console errors and failed `/api` calls. Copy any `problem` line into Findings.

**3b. Scenarios**, in order, with the Playwright tools, at `http://127.0.0.1:6790/#/...`. This app is ours and the preview data is
disposable (`-Reset` rebuilds it), so you may test it **freely and like a hostile, impatient user**, not only along the plan:
- Read the page with `browser_snapshot` (it writes an accessibility tree to a file: Read it) and **look** with
  `browser_take_screenshot` (a PNG in `local-state/pw/`; Read it as an image and describe what you see: layout, overflow, overlap,
  contrast, whether it still looks like the rest of the app). Take a screenshot after every state that matters.
- Use real events: click, type (also wrong input: empty, very long, quotes, emoji, Vietnamese accents), press Enter/Escape/Tab,
  hover, drag, double-click, browser back/forward and reload in the middle of a flow, two tabs on the same page.
- Check what a user cannot see: `browser_console_messages` (errors and warnings) and `browser_network_requests` (any 4xx/5xx, a
  request fired twice, a request that never ends) after each scenario. A request the plan did not mention is not automatically a
  finding: open it with `browser_network_request` and read its **body**; compare it with the plan's "Requests you will see" line. If
  it is not covered there, record it as a `note` with the method, path and body, and say that session 1 must confirm (that is
  enough; do not judge it); `browser_evaluate` to read the DOM or `localStorage`
  when a screen looks wrong.
- Try the sizes: 820 px wide (the target), then `browser_resize` to 1100 px (landscape: tables widen) and 390 px (a phone: nothing
  may be cut off or need sideways scrolling). Touch targets must be at least 44 px.
- Compare each scenario's screen with **Must see** / **Must not see** word by word. Anything else that is wrong is a finding
  too: an error banner, a button that does nothing, text cut off, a spinner that never ends, a console error, a slow response.
- You may read the app's API with `curl` at `http://127.0.0.1:6790/api/...` to see what the server says (GET only; the app itself
  does the writes), and read `local-state/preview/logs/worker.log`.

Limits (the only ones): you do not edit repository files; you do not read `local-state/secrets/`; and in **live** mode the key is
the user's money: a box that shows a price (Estimate / Confirm) is a spending point. Press Confirm **only** for an action listed
under "Money", at most once each, and only if the price shown is within what the plan allows; if it is higher, do not confirm and
write a finding with the number shown. Do not press Retry all, Extract, Try or any other spending button the
plan does not list, and stay within the plan's call budget (the worker also enforces `-MaxCallsPerDay`). In fake mode nothing
is spent and nothing can be broken for good.

Waiting for a real model: up to 3 minutes per job, watching Activity. A job that fails or stalls is a finding: copy the error and
the job state; retry at most once, and only if the plan says so. When the plan wants a translation judged, copy the first 5 lines
of the result next to the source lines, verbatim. After a crash or a blank screen: record it (with the console and network
output), then continue with the next scenario; if the app is dead, say so and stop (`Status: blocked`).

**Not testable here:** copy the plan's lines as NOT TRIED. Never claim them tested.

## 4. Write the findings

Edit only `## Progress`, `## Findings` and the Status line of `TEST-PLAN.md`, in the format in the HTML comment there: findings worst-first
(blocker, bug, ux, note), then Scenarios, Gates, Run. Verbatim text. Then `Status: done` (or `blocked`). In **live** mode, in Run
write the model calls used (Activity shows them) and which money actions you did.

## 5. Reply to the user

Vietnamese, at most 6 lines: gates; `n PASS / n FAIL / n BLOCKED / n NOT TRIED`; the finding titles with their severity; the plan
path; that the app is still running at **http://127.0.0.1:6790** to click (the live one uses the real key and its call cap, tell
them). Say plainly what was NOT TRIED. Tell the user that session 1 reads it with "đọc kết quả test".

## Depends on

`scripts/preview.ps1` (`-Reset`, `-Live`, `-KeyFile`, `-MaxCallsPerDay`), `scripts/smoke/smoke.mjs`,
`apps/server/tests/preview_seed.py`, the findings format in `.claude/skills/test-plan/template.md`. If one stops matching the repo,
say so in the Run block; fixing them is session 1's job.
