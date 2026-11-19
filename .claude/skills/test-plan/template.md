# Test plan: <short name of the change>

<!-- Session 1 (heavy model) writes everything above "## Findings". Session 2 (light model, drives the browser itself) writes
     only "## Progress", "## Findings" and the Status line. Plain language, short sentences: a small model must understand it without having
     seen the diff. Write in Vietnamese or English; quote UI text exactly as it appears on screen, in `backticks`. -->

- **Status:** pending            <!-- pending -> running -> done | blocked -->
- **Plan id:** <yyyy-mm-dd-short-slug>
- **Written:** <yyyy-mm-dd hh:mm>, commit `<short sha>`, working tree <clean | dirty: N files>
- **Mode:** fake | live          <!-- fake: no model calls, 2 episodes pre-translated. live: real queue worker and the user's real Gemini key, nothing pre-translated, calls cost money -->

## What changed (for someone who has not read the diff)

<one short paragraph: what the user can now do or see, and what could have broken next to it>

## Setup

- Gates: Python <yes | no: reason>; Web <yes | no: reason>
- Start command flags: `-Reset` <fake> | `-Reset -Live -MaxCallsPerDay <n>` <live>
- Quick sweep of every screen first: yes | no

## Data you will find

<!-- Facts about the seeded app so session 2 does not guess. Fake: series "Arcane Demo" (id 1; episodes 1-2 translated and
     current, episode 3 missing), series "Second Demo" (id 2, own glossary), a movie, glossary of series 1 has 2 versions.
     Live: same media, but NO episode is translated; the model will do it. Add whatever the scenarios rely on. -->

## Money (live mode only)

<!-- The only actions allowed to call the model, each at most once, with the most the price box may show before you must
     stop and NOT confirm. Everything else that would spend: do not press it. Total budget for the run: n model calls. -->
- <action, e.g. Request episode 3 -> Vietnamese; price box must say at most ~N calls>

## Scenarios

<!-- Few (up to 8), each a real risk. For each: where to start, what to do in plain words, what you must see. Say "must NOT see"
     where that matters. If you cannot say what correct looks like, the scenario is not ready. -->

### S1 <title>
- **Start at:** `#/series/1/glossary` (the address is `http://127.0.0.1:6790/#/...`)
- **Do:** <plain steps: press `History`, then press `Restore v1` ...>
- **Must see:** <exact text or state>
- **Must not see:** <optional>
- **Requests you will see:** <the calls the screen makes by itself, with method, path and the part of the body that matters, e.g. `POST .../glossary/restore` with `dry_run: true` when the sheet opens, and **no** request without `dry_run` until the button is pressed. Say what must NOT be sent. A small model cannot know a dry run from a real write>
- **Look out for:** <optional: a broken layout at 820 px wide, a button that does nothing, text cut off, an error banner>

## Not testable here

<!-- What this change touches that the preview cannot show, and which unit tests cover it. Session 2 lists these as NOT TRIED. -->
- <thing>: <why>; covered by <tests>

## Progress

<!-- Session 2 appends one timestamped line after every step (gates, app up, each scenario, long waits) so the user and session 1
     can see where it is. Nothing else goes here. -->

_not started_

## Findings

<!-- Session 2 fills this and nothing else. One block per finding, worst first, then a short run summary. A finding is anything
     that differs from "Must see", anything that looks wrong even if the plan did not say so, and any error. Do not guess the
     cause; copy what you saw.

### F1 [blocker | bug | ux | note] <one line>
- **Scenario:** S1 (or "outside the plan")
- **I did:** <the steps, short>
- **I saw:** <the text on screen or the error, verbatim>
- **Expected:** <from the plan, or what a user would expect>
- **Evidence:** <full path of a saved screenshot if the tool saved one; otherwise describe the screen in two sentences>

### Scenarios
S1 PASS | FAIL (F1) | BLOCKED (why) | NOT TRIED
...

### Gates
Python: <counts, failing test names + message> | skipped
Web: <...> | skipped

### Run
- Commit `<sha>`, working tree <clean | dirty N files>; differs from the plan: yes/no
- Mode fake/live. Live: model calls used (from Activity or the usage numbers), money actions done
- Console left running at http://127.0.0.1:6790: yes/no
-->

_not run yet_
