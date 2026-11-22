---
name: test-plan
description: Session 1 (heavy model, writes the code) hands testing to session 2 (a light model that drives the browser) through a short plain-language markdown plan - write the plan after finishing a change, or read the findings session 2 wrote. Use when the user says "viết kịch bản test", "viết test", "test case", "test cases", "lập test plan", "bàn giao test", "/test-plan", or "đọc kết quả test", "đọc finding". In this project "viết test" or "test case" means THIS plan, not unit tests. Does not run any test itself.
---

# Test plan (session 1 side)

**Which test is meant.** Unit tests (Python `unittest`, web `vitest`) are never a separate request: they are written with the
code of every change and phase (`implement-phase` §3) and must be in the diff before it is called done. When the user says
"viết test", "test case" or "kịch bản test" after a change, they mean *this* skill: the browser hand-off plan below. Do not
answer it by writing more unit tests; if the unit tests of the change are missing, say so and add them as part of the change.

Two sessions, one file. This session **writes** a plan in plain language; session 2 (light model, `/web-test`) opens the real app
in a browser, follows the plan, and writes **findings** into the same file; this session then **reads** them. Spend heavy tokens
on deciding what is worth checking and what correct looks like, not on scripting clicks. Do not write Playwright code: session 2
clicks by itself. Run the tests yourself only if the user says so.

The file: `local-state/handoff/TEST-PLAN.md` (git-ignored; one current plan). Template: `template.md` next to this file.

## A. Write the plan ("viết kịch bản test")

1. **Know the change.** `git status --short`, `git diff --stat`, read the diff that matters. Note the commit and whether the tree
   is dirty. Do not write from memory of the conversation alone.
2. **A plan already there?** Status `pending`/`running`: ask before replacing. `done`/`blocked`: move it to
   `local-state/handoff/archive/TEST-PLAN-<plan id>.md` first.
3. **Fill the template.** What makes it runnable by a small model:
   - *Short and concrete*: a scenario is Start at / Do / Must see (/ Must not see / Look out for). UI text quoted exactly, as the
     screen shows it. No "verify it works". If you cannot state what correct looks like, the scenario is not ready.
   - *Choose the mode*. **fake** (default): no model calls, free, fast, two episodes pre-translated by a fake provider - enough for
     anything that is only UI or bookkeeping. **live**: the real queue worker and the user's real Gemini key, nothing pre-translated;
     use it only when the change is about what the model returns or how a job runs (translation, retranslate, extract facts,
     proposals, estimate and price boxes, quota and error handling, glossary/style effect on real output).
   - *Live needs a "Money" section*: list the only actions allowed to call the model, each at most once, and the largest price the
     price box may show before session 2 must stop and not confirm; set a total call budget (also passed as `-MaxCallsPerDay`,
     a hard cap in the worker). Keep it small: the demo subtitles have 12 lines. The key is in `local-state/secrets/gemini.key`
     (the user puts it there; never read, print or copy it, and never write it in the plan).
   - *Data you will find*: say what is seeded (`apps/server/tests/preview_seed.py`) and what each scenario relies on. If the change
     needs a row the seed lacks, **extend the seed first** and say so.
   - *Cover*: the happy path of each changed screen, the edge the change was about (empty, zero, refused input, an error), one
     thing next to it that must not have changed, and a quick sweep of every screen. Up to 8 scenarios.
   - *Requests*: for every screen that talks to the server, say in the scenario which requests it makes by itself and which it must
     not (for example `dry_run: true` only, until a button is pressed). A light model sees only that a POST happened; without this
     line it files a note and wastes your time (that is what happened with the Restore and Copy sheets).
   - *Honest scope*: under "Not testable here" list what the preview cannot show (Bazarr, auth, the Organizr iframe, in fake mode
     every model call) and the unit tests that cover it.
   - *Gates*: Python / Web yes or no from the diff (docs only: no, with the reason).
   - Status `pending`; `## Findings` stays `_not run yet_`.
4. **Hand over.** Tell the user the plan path, the mode, the number of scenarios, and what to type in session 2: `/web-test`
   (session on the light model: `/model`). For live: remind them that `local-state/secrets/gemini.key` must exist and that the
   run can spend up to the stated budget. Do not start the preview or run gates here. Do not commit the plan.

## C. Cross-check the criteria (before writing the plan, or when closing a phase)

The plan is only as good as the list of what must hold. Before step A.3, and again when a phase is closed:

1. Open `docs/design/SPEC.md` section 6 (the numbered acceptance criteria) and the "Kết quả kiểm chứng" cell of the phase in
   `PLAN.md`. Pick the criteria the change can affect.
2. For each, name the test that covers it (`apps/server/tests/`, `packages/subcore/tests/`, `apps/web/src/**/*.test.ts(x)`).
   A criterion with no test is either missing a unit test (add it to the change) or something only a browser can see (put it in
   the plan as a scenario). Say which, in one line each; do not leave a criterion unaccounted for.
3. The read-only `qa` agent (`.claude/agents/qa.md`) does steps 1-2 on a diff and returns the table; use it when the change is
   large. It does not replace the browser run.

## B. Read the findings ("đọc kết quả test")

1. Read the plan file. `pending`/`running`: session 2 has not finished, say so.
2. Read `## Progress` for the timing and for steps it skipped. Summarise in Vietnamese: gates, each scenario PASS/FAIL/BLOCKED/NOT TRIED in one line, then the findings, worst first. A light
   model's PASS is a claim, not proof: for the scenarios about the main risk, look at the evidence (screenshot path, or the text it
   copied) and, if the app is still up on 127.0.0.1:6790, check it yourself with a request or a look.
3. Live runs: report the calls and money used against the budget; read translation excerpts it copied and judge them.
4. Check the Run block: if the commit or tree differs from the plan, the findings may be about older code.
5. Sort each finding: bug in the change (fix it), bug or ambiguity in the plan (fix the plan, tell the user session 2 reruns),
   environment/flaky (say why), not a bug (say why). Do not claim as tested what is NOT TRIED.
6. To rerun after a fix: write a new plan, or for a small fix edit the failed scenarios, set `Status: pending`, clear their
   findings and add "rerun: S2, S3" under Setup.
