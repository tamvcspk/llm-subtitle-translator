# Unreleased
- **P14: proposals from story facts.**
  - New `subcore.suggest` (pure): from the facts of the episodes in story order it finds the pairs whose relationship the facts
    show (one `address_rule` candidate per stretch of the story, with `from`/`until` at the cue where it turned, and the cue,
    its timecode and its text as evidence), the proper names in two or more episodes that the glossary lacks (`term`) and the
    characters in two or more episodes (`character` with a card). One cheap call per kind turns them into the target
    language's forms; an unusable answer costs only that kind. Timecodes and cue numbers are never sent to the model.
  - New `subserver/proposals.py` (`FactsProposer`): gathers the facts, source cues, glossary and what the inbox holds, and hands
    `Proposal`s to `SuggestionService` (`source` `facts` or `memory`); what the inbox has in any status is not asked about
    again, so a rejected proposal is not paid for twice. It runs after a job extracted facts (published first, errors swallowed:
    it never fails or loses a job) and on request: `POST /api/suggestions/generate` (`estimate_only` for the call count).
  - Setting `suggest_from_facts` (default on). Facts give no research sources, so `auto_accept_terms` never applies them.
  - The call record of these calls has prompt version `suggest-s1`. `StoryMemory.plan` reports which units it extracted.
- **P13: suggestions and the Review inbox.**
  - New table `suggestions` (migration 13: `source`, `fingerprint`, `UNIQUE(series_id, target_lang, fingerprint)`) and
    `SuggestionService` (`subserver/suggestions.py`): `propose` (skips what waits, was decided or the glossary already says),
    `accept` (optionally after an edit), `reject`, `undo`, and `bulk` for one series with the confirmed count. Accepting writes
    the glossary in the database (a character with its voice writes the card in the same transaction) and counts the
    translations it flags stale with the number the Glossary page uses; nothing is retranslated. A rejected suggestion is not
    proposed again; every `source` (`facts`, `memory`, `feedback`, `research`) goes through the same code.
  - API: `GET /api/suggestions`, `GET /api/suggestions/summary`, `POST /api/suggestions/{id}/accept|reject|undo`,
    `POST /api/suggestions/bulk` (all with `dry_run` where they write). See `docs/API.md`.
  - Settings: `auto_accept_terms` (on; a term is applied only when two independent sources agree), `auto_accept_address_rules`
    and `auto_accept_characters` (off). An applied suggestion shows in the history marked *Auto* and can be undone.
  - **Bootstrap of `auto_request`** (`bootstrap.py`, setting `auto_request_bootstrap`, default 2): a series with nothing approved
    yet gets only its first episodes from the policy; the rest stay Missing with the reason *Waiting for glossary review* until
    the suggestions are reviewed or the admin presses **Continue** (`PUT /api/catalog/series/{id}/bootstrap`). The admin's own
    requests are never held. Targets carry `reason`, the series page `series.bootstrap`.
  - Console: **Review** page (`#/review`, grouped by series: evidence with cue and timecode, the live count of translations
    accepting would flag stale, Accept · Edit · Reject, Accept all in series with the number and the count before it, History
    with Undo), a nav entry with a badge of the pending ones (hidden until the machine has proposed something), the
    Continue notice on the series page, and the three `auto_accept_*` switches and `auto_request_bootstrap` in Settings.
- **Code review of P12b: fixes, and the default target language is now Vietnamese.**
  - Defaults: `DEFAULT_TARGET_LANGUAGES` is `vi` (Vietnamese) instead of `zh`, and so are the "no target on the job" fallbacks
    (`target_output_path`, the queue and the translation attempt) and the legacy `GST_TARGET_LANGUAGE` default. A worker that
    already has target languages saved keeps them; Vietnamese is also in the bundled language list used when Bazarr cannot be read.
  - Corrections: switching the kind (Note, Cue edit, …) clears what was typed, so a note can no longer land as a cue's new text.
  - Subtitles: the page starts again when the target language changes; Prev/Next in the cue sheet keep a half-written line.
  - Style form: the live impact is debounced on stable text (it used to re-render every 400 ms for as long as it was open).
  - Settings: a save starts its group again from what the worker stored (a blank path row or a trailing space is gone) and
    forgets the kept draft; a series with no target language says so instead of spinning in Glossary, Characters and Style.
  - **Characters are saved atomically**: `PUT /api/series/{id}/characters` (and the movie form) writes the Name (glossary) and
    the Voice (card), or deletes the card with the Name rewritten, in one transaction after validating both.
  - `PUT /api/units/{id}/style` no longer guesses `vi` when there is no `lang` and no translation: it asks for `lang`.
  - `GET /api/queue` resolves the titles of every job in one query (it used to read two rows per unit on every poll).
  - Origin check: it understands a proxy that rewrites `Host` (`X-Forwarded-Host`), and with `CONSOLE_COOKIE_SAMESITE=none`,
    where it is the only CSRF guard, a write with no `Origin` must say `Sec-Fetch-Site: same-origin`.
  - Removed the leftover `Debug.test.tsx` and the unused `Placeholder` page.
- **The console is now the React app; the old vanilla-JS console is removed (P12b).** The Docker image serves the build of
  `apps/web` as `/app/static` (it no longer carries `apps/server/static/`, nor a `static-next` copy). `login.html` moved to
  `apps/web/public/` and stays the one self-contained page served without a session; a test now walks a build-shaped folder
  file by file and checks every other file answers `302`/`401` without one. Removed with the old pages: `GET /api/wanted`,
  `GET /api/scan`, `POST /api/enqueue-scan` and the `scan_limit` setting (an old backup that still holds it restores, the key
  is ignored), plus the code only they used (`list_wanted_items`, `scan_source_subtitles`, `ConsoleActions.enqueue_found`).
  `scripts/start-local.ps1` runs the build in `apps/web/dist` (it was still pointing at `apps/worker`).
- **Console in an iframe of another site: `CONSOLE_COOKIE_SAMESITE`.** The session cookie stays `SameSite=Strict` by default,
  which works while Organizr and the console share a site. With `CONSOLE_COOKIE_SAMESITE=none` it is `SameSite=None; Secure`
  behind HTTPS (a proxy that sends `X-Forwarded-Proto: https`) and `SameSite=Lax` over plain HTTP, where a browser would
  drop `None`. The login, the sliding refresh, the sign-out and a password change all set it the same way. The `Origin`
  check on writes is then the only CSRF guard; a test shows a write from another site is still refused.
- **P12b: Backups and Logs in `apps/web`** (`#/backups`, `#/logs`, reached from Settings → Maintenance). Backups: **Create
  backup** (keys are left out unless "Include keys" is ticked), **Import backup…** from a zip (a question first, the file is
  sent as it is, and the answer says the settings you had are kept in the `pre-import` backup), and the list with a download
  link the server serves. Logs: the tail of the worker's log, filtered by level (this level and worse) and by a search of the
  line and its trace, refreshed every ten seconds, with **Clear logs** behind a confirmation. With these every screen of the
  P12b order exists in `apps/web`.
- **P12b: Settings in `apps/web`** (`#/settings`). Seven folding groups (Connections and Translation open), each with its own
  Save and *Unsaved changes*; a Save sends only the keys of its group and the page shows what the worker answered. Fields
  follow the types and limits of UX Appendix B: a number below its minimum is refused before it is sent, the two numbers the
  worker keeps as text stay text, `log_level` and `log_prompt_bodies` are choices, models come from the provider's list (Strong,
  Research, Memory and Summarize can be emptied), the quota's midnight is a time zone. **Connections:** Bazarr's address, five
  keys (last four characters and status; paste a new one, or **Test** the stored or the typed one), Gemini or
  OpenAI-compatible with its Base URL. **Translation** lists the source and target languages (add from Bazarr's list; one of
  each stays ticked); **Library** has the media roots; **Maintenance** has **Clean up now** (a preview, then Confirm) and the
  way to Backups and Logs; **Security** changes the password. A half-filled group is kept in `sessionStorage` while the
  person signs in again (never a typed key) and forgotten once it is saved or put back by hand.
- **P12b: Activity in `apps/web`** (`#/activity`). Providers (state, usable, failures, reason, until), Model layers (done and
  failed per model, escalated, and how many days back the counts reach), the catalog with **Sync now**, and the jobs in five
  tabs named **Requested · Running · Waiting · Failed · Done** with the real count on each (the list holds the latest 100 and
  says so). A job is named by its series, episode and language (an older one by its subtitle file) and links to its episode;
  a running job shows `translating 120/340`, a waiting one what it waits for (`quota`, `provider`, `context (needs S01E04)`,
  …) and when it tries again, a failed one the last line of its error. **Cancel** and **Stop** act at once; **Retry**,
  **Retry all failed** and **Request that episode** go through the price box; **Export job spec** is a server link plus Copy.
  Backend: each job of `GET /api/queue` carries `title` and `label`; `POST /api/queue/retry` takes `job_ids` or `all` to
  retry for real (`{ok, count, skipped}`); `POST /api/queue/request-prerequisites` takes `estimate_only`.
- **P12b: Context of a series in `apps/web`, and Story memory in its header.** The header of a series now has **Story memory**
  (Default / On / Off) and **Specials after** (`S02E05`, checked before it is sent), saved together with **Apply**, which
  appears only when one of them changed ("Existing translations are never redone"). The **Context** tab
  (`#/series/:id/context`) lists the episodes in story order with `Source ✓ · Facts ✗`, each linking to the episode's own
  Context tab, says how many have a source subtitle but no facts and offers **Extract facts (N episodes)** through a price box
  (N model calls with the memory model); one `POST /api/units/{id}/context/extract` from the last episode that lacks facts
  reads them all, and the box then says which episode was read, which failed and which had no source. It says when story
  memory is off for the series (its own switch, or the global one it follows). No API change.
- **P12b: Glossary and Characters in `apps/web`, and Movie Glossary.** The **Glossary** tab of a series
  (`#/series/:id/glossary`) edits Terms (source, target, Locked, note, From/Until episode) and Forms of address (A calls B, B
  calls A, From/Until) as cards; fields the form does not edit (aliases, the characters, `style`) are sent back as they were.
  "Saving flags N episodes stale." is counted live from a dry run and **Save** asks only when N > 0; a glossary the worker
  refuses shows the path it names (`terms[0].from: …`) and keeps Save off. Bottom sheets: **History** (Restore asks first),
  **Copy from another series** (Merge or Replace), **Import YAML** (paste or choose a file, live impact, a broken file is
  refused with its path, an empty text cannot be imported) and **Export YAML** (a server link plus **Copy YAML**; if the
  clipboard is blocked the text is selected instead). The **Characters** tab (`#/series/:id/characters`) shows one card per
  character: **Name** (an entry of the glossary's `characters`) and **Voice** (the character card), forms of address read only
  with a link to the glossary. Add/Edit writes both halves (two requests, the impact is the sum; a half is written only when it
  exists or was filled in), Delete removes the voice and then the name. A movie's **Movie Glossary →** link in the Unit header
  opens `#/unit/:id/glossary` (no From/Until, no Copy). Backend: `GET /api/series/{id}/glossary.yaml?lang=` and
  `GET /api/movies/{unit_id}/glossary.yaml?lang=` serve the saved glossary as a file download (`Content-Disposition`), so the
  console never needs a `blob:` link.
- **P12b: Compare in `apps/web`** (`#/unit/:id/compare/:version`). The current translation against a candidate, cue by cue,
  differing cues only by default ("48 of 342"), a "Pick variant" box on each, and a sticky bar ("Selected 12 from variant, 330 kept
  from current"). **Apply picked cues** records a verdict on every differing cue (picked: variant, the rest: current) and
  writes the picked ones as cue edits; **Use this version** takes the whole candidate and, for a variant, ticks "Also apply this
  variant's style to the series" by default, listing what the variant sets and counting the stale episodes from a dry run of
  `adopt` before anything is saved. A version that is no longer a candidate can only be looked at.
- **P12b: Style in `apps/web`, for a series, a movie and one episode.** One form (`StyleForm`) with every axis at **Inherit**
  ("Inherit · now Neutral", read from `effective`), Tone as free text, Dialect, Honorifics, Fast dialogue, the three limits and
  Notes behind **More**, and a Preset with the TMDB suggestion ("Apply Drama"). The line "Saving flags N episodes stale." is
  counted live from a dry run (debounced; "Checking…" meanwhile) and **Save** asks once, only when N > 0 ("Nothing is
  retranslated"). **Try 20 lines (≈ 1 call)** shows Source, Now and With this style (no Now column for a series nothing is
  translated in) and writes nothing; **Compare on a whole episode** asks the price of a variant job first and ends as a
  Candidate in History. The tab of a movie is its own style; the tab of an episode is its override
  ("Overrides the series style for this episode only", Clear episode override). Backend: `PUT /api/units/{id}/style` now takes
  `dry_run` and `lang` and answers the stale count for that episode.
- **P12b: episode and movie page in `apps/web`, Subtitles tab.** `#/unit/:id` has the shared header (code and title, state,
  `Missing units`, current version, model and time, a warning for a file changed outside the worker, **Remove output** behind a
  confirmation sheet), a target-language choice when there are several, and the tabs (a movie has no Context). Subtitles lists
  source and target cue by cue, 50 to a page, with search, **Jump to** a player time, the lock badge, and **Edit cue** in a
  bottom sheet (Prev/Next, Save locks the cue, Unlock, warnings after saving). **Corrections** adds a term, a form of address, a
  note or a cue edit (scope Episode, From here on or Whole series for the first two; a wider scope says how many translations
  it flags stale, nothing is retranslated) and lists and removes the episode's own. **Retranslate** takes the whole episode, a
  cue range or a time range, the corrections to attach, shows "Will overwrite N cues and keep M locked", and goes through the
  Estimate box (`estimate_only` on `POST /api/units/{id}/retranslate`) before it queues anything. **History** lists every
  version (Current and Candidate badges, variant label, range, model, tokens), with **Diff** (read only when asked), **Restore**
  and **Use this version** behind a confirmation sheet; Compare links to a page that is still a placeholder. **Context** shows
  the story memory the translation used, what a new one would get, "Changed" and the missing earlier episodes (read only).
  Only the episode's Style tab is still a placeholder. Back link
  goes to the episode's series, found from the catalog.
- **P12b: Series page, Episodes tab in `apps/web`.** Header with title, target language and Auto-request (saved at once);
  one row per episode with its label (`Missing · Waiting · quota`, `Translated · Stale`, …), the reason under it (resume time,
  error, `v2 · model · 2d ago`, "File changed outside the worker") and one button: Translate, Cancel, Stop, Retry, Request that
  episode, Open Connections, Retranslate, Improve, History, View, Translate again, Stop ignoring. Translate season, whole
  series and the ticked episodes all go through the Estimate box, Retry too (`/api/queue/retry` with `estimate_only`); Cancel,
  Stop and Ignore cost nothing and do not. Status chips follow `?status=` (the Home "View failed" link). Not yet: Restore (use
  History on the episode page, later), the Candidate badge, Memory and Specials after in the header (they come with Context).
  The API reads one more field: each target of `GET /api/catalog/series/{id}` carries `job_info` (job id, progress, resume time,
  error, the earlier episodes a context wait is for). Client: a 4xx answer is not retried.
- **P12b: Home in `apps/web`.** Search by title (ignores case and accents), the five chips, one next-action button per row
  (View failed, Translate missing or Improve), empty, no-match and error states, and Sync now. **Translate missing** goes
  through the shared Estimate box: it asks for the price first (`estimate_only`) and makes jobs only on Confirm. Routes are
  `#/library`, `#/series/:id/*` and `#/unit/:id/*` (the last two are placeholders). Still not served.
- **P12b: `apps/web`, the new console's frame (not served yet).** TypeScript, React 19, Vite, Mantine, React Router in hash
  mode, TanStack Query and Tabler icons, all bundled (fonts included, no CDN), with Vitest, ESLint and a lockfile
  (`npm ci`). The shell is there: Library, Activity and Settings in the nav (the screens are placeholders), the global
  banners (a refused key or unknown model, a catalog that Bazarr could not refresh with **Sync now**), the status strip
  and the blocking *Your session expired* overlay. The Dockerfile has a Node build stage and puts the result in
  `/app/static-next`; `/app/static` is still the old console and nothing serves the new one until its main path is done.
  `login.html` now sends a person back to the `#hash` they were on (only a fragment is accepted). CI has a Web job and
  Dependabot watches npm. The server also gives `.woff2` files their own content type.
- **P12b backend: cost before you spend, and "Use this version" keeps the style.** `estimate_only: true` now works on
  `POST /api/queue/retry` (one job, a list, or `all` for Retry All Failed), `POST /api/units/{id}/variant` and
  `POST /api/units/{id}/retranslate` (a range costs its own cues); the answer has the same `estimate` as a bulk request and
  nothing is queued, saved or called. `POST …/versions/{v}/adopt` (and `…/choose` with a whole-episode win) takes
  `apply_style` to keep the variant's preset and style as the series' (or the movie's) own in one request, and `dry_run` to
  count first how many other translations that would flag stale; nothing is retranslated.
- **P12b backend: a movie has its own glossary, style and character cards** (`/api/movies/{unit_id}/glossary`, `…/glossary/history`,
  `…/glossary/restore`, `…/style`, `…/style/preview`, `…/cards`, `…/cards/delete`; `docs/API.md`). They are the stored rows of
  owner `movies/<movie key>`, so nothing reaches a series, and the stale count is for the movie's one unit. Before this a movie
  could only get glossary terms through corrections.
- **P12b backend: `GET /api/catalog` adds `failed` and `ignored`** per series and for movies, per target language (for the
  Failed and Ignored chips of the new Home). They equal what the labels of the series page add up to. The `Failed` label is now
  read by (unit, language) instead of by the file-path key, so a request whose files were renamed (Sonarr) still shows its
  earlier failure. A failed retranslation already counted before (it shares the key of a normal job); a failed variant, which
  has its own kind, still does not.
- **P12: style profile, glossary in the database, trial of a style, variants (FR-18, ADR-14).** **Upgrade note:** glossaries
  moved from `series/<key>/glossary.<lang>.yaml` into `app.db`. At the first start the worker reads those files into the
  database once and leaves them where they are; **from then on editing a file changes nothing**: use the Glossary page
  (a form, with the YAML as an import/export tab) or copy `app.db` to back it up (console backups do not hold the database).
  Every save keeps what it replaced in a history with *Restore*, which replaces `backups/glossary/`; `retention_glossary_keep`
  now counts those rows and the cleanup no longer touches a leftover `backups/glossary/` folder. A glossary can be copied from
  another series (a copy, not a link). **Style** (new page per series, `#style/<series>/<lang>`): formality, tone, swearing,
  idioms, dialect, anime honorifics and subtitle limits (characters per second, line length, lines per cue), in layers that
  override each other field by field: the language (built in), a genre **preset** (sitcom, drama, anime, documentary, kids;
  yours to edit or clone; suggested from TMDB's genres), the series, an episode, and **character cards** (how a character talks
  and addresses others, catchphrases, sample lines; used in an episode whose dialogue names them). The glossary still beats
  every layer in the prompt. Changing a style, a preset in use or a card flags the translations made without it as stale and
  retranslates nothing; **Check** counts first. **Try this style** translates about twenty sample lines, or the cues you
  name, with the form as it stands (saved or not), next to what the episode has now, and writes nothing. **Compare on a whole
  episode** queues a `variant` job that keeps the episode translated with another style as a candidate; the Compare page shows both
  line by line, records which you prefer as `ab` quality signals (by style and prompt version), and can turn your picks into cue
  edits or adopt the whole candidate. **Example lines**: cues you corrected elsewhere in the series that look like the ones
  being translated are shown to the model, within `few_shot_budget_tokens` (600, 0 = off); they are in the bundle and its hash but
  not in the rules hash, so fixing one cue never marks a series stale. A glossary with more than `glossary_filter_threshold`
  (50) terms lists in each request only the terms its lines use; forms of address and character cards are never trimmed. A
  version made by retranslating a range now says the range (`params`) and is stamped with the rules of the whole episode, so only
  the other episodes stay stale. Bundle version 2 (style and voices; a version-1 bundle still runs) and prompt version `p12-1`: every
  translation made before is *not* flagged stale by the new prompt, but a new translation uses it. Schema 12 (`glossaries`,
  `glossary_history`, `style_profiles`, `character_cards`, `translations.params_json`). New endpoints: `GET|PUT /api/series/{id}/style`,
  `GET /api/series/{id}/style/suggestions`, `POST /api/series/{id}/style/preview`, `PUT /api/series/{id}/cards`,
  `POST /api/series/{id}/cards/delete`, `GET /api/style/presets` (+ `PUT`, `clone`, `delete`), `GET|PUT /api/units/{id}/style`,
  `POST /api/units/{id}/variant`, `GET /api/units/{id}/versions/{v}/compare`, `POST /api/units/{id}/versions/{v}/choose`,
  `GET /api/series/{id}/glossary/history`, `POST /api/series/{id}/glossary/restore|copy`; `GET|PUT /api/series/{id}/glossary` now
  carry a `doc` beside the `yaml`.
- **P11: bundle and declarative CLI (FR-21, ADR-16).** The worker gathers one bundle per translation (`subserver/gather.py`) and the engine runs only bundles. `subcore.bundle` is plain data with a `bundle_version` and a `bundle_hash`, and `render(bundle)` is the one place the prompt is built, so the worker and the CLI send the same bytes. Every job keeps the bundle of its latest attempt (schema 11: table `job_bundles`, swept with its job by every delete and by clean-up, so queue queries never read it), and **Export job spec** (`GET /api/jobs/{id}/spec`, a queue row action) downloads it. A key that reached the text (a pasted overview, say) is masked before the model, the call records and the spec see it, so the spec reproduces the run. Every translation call records its `bundle_hash` (NFR-12), and the job snapshot names it. The `subcore` CLI gained `bundle`, `render-prompt` (no provider is built), `validate` (exit 1 on review errors) and `compare` (two bundles, or one against a golden set: a table of numbers, no model judging). `translate` takes `--bundle`, writes a report JSON beside the output, and reads the API key from the flag, the environment or `--api-key-file`, in that order. Worker and CLI share the review (`translate/review.py`): count, tags, locked terms, line length, reading speed and untranslated cues. `subcore` imports neither the worker nor SQLite (test-enforced). Old bundles without the optional fields still run.
- **Structure rename (P0e).** No behaviour change. `apps/worker` is now `apps/server`, the Python package `gst_worker` is `subserver`, and the workspace member `llm-subtitle-worker` is `llm-subtitle-server`. The image keeps `/app/worker.py`, `/app/static` and all environment variables; only `/app/gst_worker` became `/app/subserver`, so a bind-mounted override of that folder (not a normal setup) needs the new path.
- **P10: Bazarr is read, not pushed (FR-14, FR-15.4-15.6, ADR-10).** **Upgrade note:** nothing is translated by
  itself any more. The worker now *pulls* the library from Bazarr's API (series, episodes, movies, their subtitles
  and the languages Bazarr lists as missing) every `catalog_sync_interval` seconds (300) and on **Sync now**, and
  keeps it as a cache; a read never creates a job. A read that fails, times out, comes back empty or lists more than
  `catalog_shrink_guard` percent (20) fewer items is set aside: the catalog stays as it was and Library says when it
  was last read, with the warning. Library now browses everything Bazarr lists and shows, for each language of each
  item, a state worked out on reading: Missing, Translated, Edited, External, Outside profile, with Removed, Stale,
  Requested, Running, Waiting (reason), Failed and Ignored. You request a translation of an item, a selection, a
  season or a series (`POST /api/jobs`, `mode` `fill` or `replace`); a request for more than one job shows an
  estimate (jobs, cues, model calls, what is left of `max_calls_per_day`, the time at the recent pace) and asks you
  to confirm, and asking again for the same item makes no second job, even after Sonarr renamed the files (an item
  is identified by Bazarr's id; a waiting job follows its files). **Ignore** says "do not translate this language
  of this item". **Auto-request** (`auto_request`, off by default; per series on/off on its Library page;
  `auto_request_daily_cap`) makes jobs by itself, after an accepted read, for what Bazarr lists as missing, except
  Ignored or Removed items, items with a job and anything outside `GST_ONLY_PATHS`; its jobs have origin `policy`
  and never bring back a subtitle you removed. If you relied on the Bazarr script translating everything, turn
  Auto-request on, for one series first. **Removed:** `gst_enqueue.sh` and Bazarr's post-processing command (see
  docs/BAZARR.md for taking it out), the `/bazarr-postprocess` mount, `GST_MAX_PENDING`, `admission_mode`, the
  `held` and `rejected` job states, the Queue → Held and Rejected tabs and `/api/queue/held|rejected|approve|
  reject|restore`. At the first start the old queue settles once, idempotently: `pending`, `deferred` and
  `processing` files become jobs, `done` and `failed` become history, **held** jobs are dropped (the item is Missing
  again) and **rejected** ones become Ignored; the old folders move to `queue.migrated/`. New endpoints:
  `GET /api/catalog`, `/api/catalog/status`, `/api/catalog/series/{id}`, `/api/catalog/movies`,
  `POST /api/catalog/sync`, `PUT /api/catalog/series/{id}/policy`, `POST /api/catalog/ignore|unignore`,
  `POST /api/jobs`. Schema 10 (`catalog_items`, `catalog_state`, `unit_decisions`, `series.auto_request`).
- **P9: file clean-up (FR-13).** The worker now deletes what it made for itself and no longer needs, once a day
  (with the backup check) and on **Clean up now** on the Backups page, which shows a preview and asks first. Four
  new settings, live at once, `0` switching a group off: `retention_done_days` (30; done, skipped and cancelled job
  rows), `retention_failed_days` (90), `retention_versions_keep` (5: the stored copies of older subtitle versions,
  keeping the current one, the first one and the newest N) and `retention_glossary_keep` (20 backups per series and
  language); leftover `.*.tmp` files older than a day in the queue and data folders always go. A version whose
  copy was deleted stays in History as **pruned**: it cannot be restored or compared, and restoring it answers
  `400` with the reason. Never touched: media folders (a leftover beside a subtitle is only listed), jobs that are
  not finished or held, rejected jobs, current versions, story memory, the database. `POST /api/cleanup` takes
  `{"dry_run": ...}` and previews unless it says `false`; a dry run and the real run after it report the same
  numbers. `model_layers` in `GET /api/status` now carries `window` (how far back finished jobs are kept). The
  summary is logged without names or subtitle text.
- **P8: jobs and versions in SQLite (FR-15, FR-17, ADR-11, ADR-12).** A translation job is now a row of the `jobs`
  table, not a JSON file. The worker claims one job atomically (`BEGIN IMMEDIATE`), orders them by class
  (interactive > retranslate > auto), round-robin per series, with aging, and skips jobs whose model cannot be
  called now. Leases with heartbeat recover a crashed job on the next start (`max_crashes`, then `failed` with
  reason `crashed`). A provider failure never fails a job: it waits with a reason (`quota`, `provider`, `context`,
  `settle`, `rate`) and the console shows it. A per-(provider, model) breaker opens after `breaker_threshold`
  failures in a row and rests 300, 900, 1800, 3600 s, with one trial call when the rest is over. A refused key
  (`AuthError`) or an unknown model parks the jobs until the setting changes. `max_calls_per_job` and
  `max_calls_per_day` cap the model calls. A provider's content refusal (`Blocked`, new in subcore) is a kind
  of `BadOutput` and fails the job with the reason after one escalation. The queue folders of 0.x are imported
  once into the table and moved to `queue.migrated/`; `gst_enqueue.sh` is unchanged and still drops its files
  into `pending/`, which the worker takes into the table each tick until P10. The console has a Stop button for a
  running job and a "Request earlier episodes" button for a job waiting on story facts.
  Versions (FR-16): a result that cannot go on disk safely (the file appeared while the job ran, the subtitle
  changed under a replace job, the source changed) is kept as a `candidate`, and "Use this version" publishes
  it. Deleting the subtitle from the console keeps a `removed` version (restorable by revert) and the episode
  is not requested again automatically. Publishing is announced before the rename and settled at start-up.
  A hand-edited file is kept once as an `external` version. Consecutive cue edits within
  `edit_coalesce_minutes` are one version. The Bazarr rescan after a change runs in the background with retries.
  Schema 9. ADR-12: the application's own SQLite is no longer behind ports (`AuthRepo`, `UsageLog`,
  `VersionRepo`, `MemoryRepo`, `CatalogRepo`, `SettingsRepo`, `TelemetryRepo` are gone); core code takes `Store`
  and its tests run on `Store(":memory:")`. A start-up check no longer turns a media share that is not mounted
  into removed subtitles. Not done yet: the Library label "Removed" (the flag is in
  the API, the console badge is not), and the CI run on GitHub.
- Fix: with `job_settle_seconds` at 0 a job no longer waits when its subtitle's modification time is a few
  milliseconds ahead of the clock (Windows rounds file times up).
- **P7: a second provider, `openai_compat` (FR-9.1-9.3, ADR-3).** The models can now run on any server that speaks
  the OpenAI chat completions API: OpenRouter, Ollama, LM Studio, vLLM, OpenAI. Set `llm_provider` to
  `openai_compat` and `llm_base_url` (for example `http://ollama:11434/v1`) in the settings (or `LLM_PROVIDER` and
  `LLM_BASE_URL`, used until something is saved); the change is live at once. The key is a stored key of provider
  `openai_compat` (field `openai_compat_api_key`, `POST /api/keys`, or the first-run seed `LLM_API_KEY`), tried in
  order like the Gemini keys, marked `invalid` when the server refuses it, and never logged; a server of your own
  needs none. **Test** on the key lists the server's models, and the model dropdown shows them. Set `gst_model`
  and `gst_fallback_model` to the server's own model names when you switch (the defaults are Gemini models).
  The adapter (`subcore.providers.openai_compat`, standard library only) streams, never follows redirects,
  and turns 401/403, 404, 402/`insufficient_quota`, 429, 5xx, timeouts and empty or non-JSON answers into the
  typed errors. A server that cannot do structured output is asked for JSON mode, then for JSON through the
  prompt alone, and every cue is still validated and repaired as with Gemini. New settings: `llm_provider`,
  `llm_base_url`, `llm_max_input_tokens`, `llm_max_output_tokens` (what the server's model allows),
  `summarize_model` and `research_model` (FR-9.3: a model for each task, each falling back to `gst_model`).
  `subcore translate --provider openai_compat --base-url ... --model ...` tries it by hand. Gemini stays the
  default and nothing changes for it. Not done: choosing the provider per model, the Settings page for it (use
  `POST /api/settings` until the UI redesign), a trial against a real Ollama or OpenRouter.
- **P6: settings and API keys in the database (FR-20, ADR-13).** Settings and the Bazarr, TMDB and
  Gemini keys now live in `app.db` (schema 7: `settings`, `api_keys`) instead of `/state/config.json`, and a
  change is live at once, with no restart: the running worker adopts it when it is saved. A saved value beats
  the environment variable (`BAZARR_URL`, `GST_MODEL`), which beats the default; paths, the port and the
  console account stay in the environment and the console cannot write them. **On the first start** the old
  `config.json` is imported once (a value already stored is kept, the removed `gst` options are left out) and
  the keys of that file, or else of `GEMINI_API_KEY*`, `TMDB_API_KEY` and `BAZARR_API_KEY`, become stored keys,
  once: changing the variables later changes nothing, so the keys in `.env` are only a first-run seed. The
  old file is left in place and no longer read. Keys have a label, a status (`ok`, `invalid`, `exhausted`) and
  a last-used time; `GET/POST /api/keys`, `PUT /api/keys/{id}`, `POST /api/keys/{id}/test` and `/remove` manage
  them, and the API returns only the last four characters of a key (nothing for a key under 12 characters).
  A Gemini key the provider refuses (`AuthError`, now also recognised from Google's 400
  `API_KEY_INVALID`) is marked `invalid`, passed over (the second key takes over), and Settings and the
  dashboard warn; pasting a new key, or **Test** on one that works, reopens it and the next call uses it
  (until P8, a job that met the refusal still fails and is retried by hand). A model the provider does not
  know (`ModelNotFound`, HTTP 404) is a typed error and a warning on Settings, not a key problem. **Backups no
  longer hold any API key** (nor the console account): the zip carries the saved settings; "Include the API
  keys" on the Backups page (`{"include_keys": true}`) adds `config/api_keys.json` for a manual backup, with a
  warning; scheduled backups never do; an import never restores the console account. The database file is
  created with mode `0600`. New `subcore.providers.ModelNotFound`. `/api/settings` gains `*_last4`, `*_status`
  and `key_warnings`. No change to the queue format or `gst_enqueue.sh`.
- **P5: event log and measurement (FR-19, NFR-11).** The worker now leaves a trace of what it decides
  and what it asks the model, in the database (schema 6) and in files, so "why did this episode not
  run?" has an answer and later changes to prompts, models or styles can be measured. (1) **Events**:
  every decision (skip with its reason, claim, model chosen, escalation, deferral, quota pause, publish,
  version, revert, cue edit, settings saved) is one row with the job or unit and a sentence of reason,
  also written to `worker.log` as one JSON line; `GET /api/events` reads them. (2) **Call records**:
  `llm_calls` also keeps the prompt version, glossary hash, call kind (translate, repair, split), part,
  HTTP status, finish reason, attempt number, cues repaired and an estimated cost. (3) **Quality signals**:
  a cue edit (before and after), a revert, a retranslation request, and every problem the validator found
  in a reply, repaired or not. (4) **Bodies**: the prompt and answer of each call go to
  `logs/llm/<day>.<n>.jsonl` (gzipped once closed) under `log_prompt_bodies` (`all`, `sample`, `errors`,
  `off`), cut to `log_body_max_bytes`, and bounded by `log_max_mb` and `log_retention_days`; the index in
  the database says "rotated" once a file is gone. Settings has a new *Logging and measurement* card;
  `log_level` takes effect when saved. Keys, passwords, cookies and `Authorization` headers are removed
  from the log, events, bodies and job error files by one shared filter. Recording is best effort on its own
  thread: a full disk or a locked database drops the record and never fails or delays a job. `subcore`'s
  `CallLog` gained the call kind, part, attempt, HTTP status, finish reason and the request and answer;
  `TranslationResult.issues` lists the validator's findings. No change to the queue format,
  `gst_enqueue.sh` or job behaviour.
- **Roadmap renumbered in execution order (docs only).** `PLAN.md` now lists the unfinished phases as
  one linear sequence, P5 to P16 (logging, settings and keys in the database, the `openai_compat`
  provider, jobs and versions in SQLite, file cleanup, Bazarr catalog pull, bundle and CLI, style
  profile, suggestions and the Review inbox, suggestions from facts, Insights, research). File cleanup
  (FR-13) moved out of P0d into its own phase, P9. `docs/design/SPEC.md` and `ARCHITECTURE.md` are v0.11:
  one `suggestions` table and one Review inbox for every source of suggestions, research kept as a
  required v1 feature but built last, and new acceptance criteria 21 to 23. No code changed.
- **Fixes from the first real Bazarr trial.** A translated subtitle was written with mode 0600 (the
  private mode of the temporary file), so Bazarr and media servers running as another user could not
  read it; it now keeps the mode of the file it replaces, or 0644, and a worker running as root gives it
  the owner of the folder. The Bazarr rescan after a translation now waits up to 180 s (Bazarr answers only
  after scanning the whole series: 31 s for 150 episodes, which outlasted the 20 s default and timed out on
  all ten jobs of the second trial); a rescan that still fails no longer fails a job whose subtitle is
  already written, and is logged as a warning.
- **P0d part 2: hold mode and job approval (FR-12).** New setting `admission_mode`
  (`auto`, the default, behaves as before; `hold`). In `hold`, the worker sets the jobs the
  Bazarr script queues aside as *held*: no model call, no quota. Queue → Held (sidebar badge,
  grouped by series, works on a phone) approves one job, the selected ones, a series or all, or
  rejects them; a rejected job is remembered so the script and scans do not queue it again, and can
  be restored. Approved jobs still skip a language that appeared meanwhile. Jobs made in the console
  are never held, and switching the mode does not move jobs already waiting or held. New API:
  `GET /api/queue/held`, `GET /api/queue/rejected`, `POST /api/queue/approve|reject|restore`;
  `/api/queue` gains `held` and `rejected`. `gst_enqueue.sh` and the job file format are unchanged
  (jobs written by the worker get an `admission` field).
- **P0d part 1 (in progress): worker ports.** Internal refactor, no behaviour change: the
  media manager port (`MediaLibrary`) now also answers "is the target still missing" and
  runs the Bazarr rescan, and a new `Metadata` port (TMDB descriptions) is passed into
  `process_job` from the composition root. Settings changed in the console now reach the
  Bazarr rescan after a subtitle edit too (before, that rescan used the URL and key the
  worker started with). Queue format, `gst_enqueue.sh`, schema and HTTP API unchanged.
  The HTTP handler moved from `worker.py` to `gst_worker/web.py` and the settings loader to
  `gst_worker/settings.py` (both still importable from `worker`); enqueueing a picked subtitle
  or a scan result is now a use case in `ConsoleActions`.
- **P0c: console login form.** Browsers now sign in at `/login` (cookie session,
  Sign out in the sidebar); Basic Auth keeps working for scripts. The account is
  created from `CONSOLE_USER`/`CONSOLE_PASSWORD` on the first run and stored as a
  scrypt hash (schema 4, table `auth`); from then on the stored account wins and the
  password is changed in Settings (this signs out every other session).
  `CONSOLE_PASSWORD_RESET=1` overwrites it after a forgotten password. Five wrong
  logins lock an address for five minutes. Sessions are signed cookies (secret in
  `app.db`, schema 5) that survive a restart and end on a password change or
  reset. **Upgrade note:** changing `CONSOLE_PASSWORD` and restarting no longer
  changes the password.
- **P4: story memory (off by default).** Before an episode is translated the worker
  can read the source subtitles of the episodes before it and tell the model who the
  characters are, how each pair stands to each other and what happened lately, so
  forms of address follow the story even when Bazarr delivers a season pack out of
  order. Facts come from source subtitles only (never from translations), are kept
  per subtitle hash, and an episode never sees the facts of a later one: episode 6
  translated first still knows episodes 1-5, episode 1 translated afterwards knows
  nothing of 2-6. When an earlier episode has no subtitle yet the job waits
  (`memory_gap_wait_seconds`, default 30 minutes, re-checked every 5), then
  translates marked `incomplete` and names the missing episodes; when facts of an
  earlier episode change or the missing episode arrives, the later translations are
  flagged `stale` (never retranslated automatically). New **Context** tab on the
  Episode page (what the translation used, what a new one would get, a per-series
  On/Off switch, where specials sit in the story, "Read facts now"), a **Story
  memory** card in Settings (`memory_enabled`, `memory_gap_wait_seconds`,
  `memory_model`, `memory_recent_episodes`, `memory_budget_tokens`), and
  `GET /api/units/{id}/context`, `POST /api/units/{id}/context/extract`,
  `GET/PUT /api/series/{id}/memory`. `uv run subcore memory ep01.srt ep02.srt ...`
  prints the extracted facts and the running relationship state with timecodes, to
  judge extraction quality on a series you know before switching memory on. The
  Checked against Gemini on six episodes each of three real series: the extraction
  prompt now says a fiancé or spouse is `romantic` (it called them `family`) and
  names the exact relationship ("mother and son", "son-in-law and father-in-law")
  because `family` alone cannot tell a parent from a sibling; names like "Ted" and
  "Ted Mosby" are merged into one character when that is unambiguous, but two
  characters of one episode never are; a doubtful reading does not overturn a clear
  one; and the command retries a busy model instead of stopping. The
  database moves to schema 3 (new `facts`, `summaries_cache`, `context_snapshots`
  tables); older databases upgrade in place.
- **Two model layers, light by default.** The default model is now
  `gemini-3.1-flash-lite` and the *strong model* (setting `gst_fallback_model`,
  default `gemini-3.5-flash`) only takes a job the default cannot finish: output
  that stays invalid after repair and splitting now moves the job to the strong
  model once (before, it failed at once); an outage or a spent daily quota still
  hands the job over as before. A job that was escalated records
  `escalated_from` and `escalation_reason`; `/api/status` has `model_layers`
  (finished jobs per model, and how many escalated jobs the strong model rescued)
  so the split can be judged and dropped if it does not pay (clear the strong
  model in Settings to run one model). Existing `config.json` files keep the
  models they already saved.
- **Translate only what Bazarr still misses, and never the whole library.**
  A target language now counts as present when *any* sibling subtitle of that
  video is in it (`Name.vi.hi.srt`, `Name.vi.ass`, ...; a forced-only track does
  not), not only the exact file the worker would write; this applies to the
  Bazarr script, console enqueue, the media scan and the worker. Before a job
  spends a model call the worker asks Bazarr whether the language is still in the
  item's `missing_subtitles`; if Bazarr says it is not, the job ends as
  `skipped-not-wanted` (if Bazarr cannot answer, the files decide). New guards for
  trying the worker against a real Bazarr, read by the worker and by
  `gst_enqueue.sh` (set them in both containers): `GST_ONLY_PATHS` (comma
  separated folders; nothing outside them is queued or translated) and
  `GST_MAX_PENDING` (most jobs waiting or running at once; `0` is no cap).
  Explicit retranslations are not affected.
- **P3: Library, Episode and Glossary pages in the console.** The worker now knows
  which series, episode or movie each job is (Bazarr ids, falling back to the file
  name) and lists them under **Library** with a status per target language:
  `current`, `stale` (the glossary or the episode's corrections changed after the
  translation was made; only flagged, never retranslated automatically),
  `incomplete`, `untranslated`. The **Episode** page shows source and translation
  side by side, jumps to a player time (`00:23:10`) or searches both texts, and
  works on a phone. A cue can be corrected by hand (applied at once as a new
  version, no model call, then locked: later retranslations keep it byte for byte),
  or a rule can be added from it: `term`, `address` (forms of address) or `note`,
  scoped to this episode (stored in `app.db`), from this episode onward, or the
  whole series (both written to the series glossary YAML). **Retranslate** redoes
  the whole episode, a cue range or a time range (the whole source is still sent as
  context, only the range is requested), ahead of automatic jobs, with the rules
  you choose. **History** lists every version with a cue-level diff and one-click
  restore. **Glossary** edits a series' YAML with validation, an atomic write, a
  backup of the previous file and a count of translations a save would make stale.
  New endpoints are in `docs/API.md`; glossary terms accept `from`/`until`.
  The worker reads `DATA_DIR/series/<key>/glossary.<lang>.yaml` for every job.
  **Upgrade notes:** `app.db` migrates to schema 2 on start (adds series, units,
  corrections; existing versions are kept but only appear in Library once their
  episode is translated again). A glossary file that is not valid YAML now stops
  the job with the reason instead of translating without it.
- Settings: removed the options that have had no effect since P2 (`gst_batch_size`,
  `gst_retry_batch_size`, `gst_skip_upgrade`, `gst_quiet`, `gst_progress_log`,
  `gst_thoughts_log`, `gst_token_report`, `gst_top_p`, `gst_top_k`,
  `gst_thinking_level`, `gst_no_streaming`, `gst_context_size`). Saving settings
  drops them from `config.json`. The nav item "Library scan" is now "Media scan".
- **P2: the worker now translates in-process with `subcore` instead of the `gst`
  CLI.** `gemini-srt-translator` is no longer a dependency. Provider errors are
  typed (daily quota, rate limit/overload, rejected key) rather than parsed from
  CLI output. Calls are paced per API key (`LLM_RPM`, `LLM_TPM`, `LLM_RPD`;
  default 10 RPM unless paid quota is enabled), `429`/`503` retry with backoff,
  jitter and `retry-after`, a second API key takes over when the first is spent,
  and a job has a 30 minute watchdog (`JOB_TIMEOUT_SECONDS`).
  **Upgrade notes:** a failed translation now restarts from the beginning (no
  `.partial`/`.progress` resume). The Settings options `gst_batch_size`,
  `gst_retry_batch_size`, `gst_top_p`, `gst_top_k`, `gst_thinking_level`,
  `gst_context_size` and the logging/streaming switches no longer have any effect.
- New SQLite store `app.db` (in `DATA_DIR`, default the state volume) records
  every published subtitle version and every model call. A result is reused
  without a model call when the source, model, temperature and description are
  unchanged. Every version has a copy in `backups/subtitles/`; a retranslate keeps
  hand-edited files as backups first and a revert restores any version exactly.
  Automatic jobs still never overwrite an existing target.
- Removed `scripts/update_upstream.py`; Dependabot now watches `google-genai`.

# 2026-11-12
- `subcore` now detects a model that attaches translations to the wrong cue
  number (seen with `gemini-3.1-flash-lite`: all ids present, text shifted by
  2–4 cues for hundreds of cues). Digit/name anchors and a sliding length
  profile flag the shifted region, which is re-requested through the normal
  repair rounds; a drift that survives them only adds a warning.
- `subcore` default Gemini model is `gemini-3.5-flash` (`gemini-2.5-flash` is
  closed to new API users).

# 2026-11-11
- Added mandatory HTTP Basic Auth for the web console and API (P0b, NFR-5).
  Set `CONSOLE_USER` and `CONSOLE_PASSWORD`; requests without matching
  credentials get `401`, and with credentials unset the console answers `503`
  with instructions while the queue worker keeps running. `CONSOLE_AUTH=off`
  opts out (warning logged at start). **Upgrade note:** existing deployments
  must add the two variables or the console stays locked.
- Added the `subcore` engine (P1, library + CLI only; the worker still runs `gst`):
  SRT parsing/validation/atomic write, a typed-error provider layer (Gemini via
  google-genai, plus a network-free fake), a translator that plans parts at
  silences, sends `{i, text}` only (never timecodes), and repairs just the
  invalid cues (2 rounds, then split), and YAML glossaries with locked terms and
  timed address rules. `subcore translate file.srt --glossary g.yaml` translates
  by hand. NOTICE now carries the MIT attribution for the prompt rules adapted
  from gemini-srt-translator.
- Converted the repository to a uv workspace (P0, no behavior change): the
  worker moved to `apps/worker/` (`llm-subtitle-worker`), a new `subcore`
  engine skeleton lives in `packages/subcore/`. The Docker image installs only
  the worker and keeps the `/app/worker.py`, `/app/gst_worker`, `/app/static`
  runtime layout. CI runs each member's tests separately.
- Fork housekeeping: the default image is now
  `ghcr.io/tamvcspk/llm-subtitle-translator:latest` (compose, `.env.example`,
  README, configuration docs); Dependabot PRs are assigned to the fork owner.

# 2026-09-07
- Centralized queue lifecycle transitions, recovery, retry metadata, quota
  admission, and error-file cleanup in the queue module; the worker supplies
  the translation execution callback.
- Unified translator checkpoint and partial-output handling for execution and
  queue progress snapshots. Added lifecycle and producer-protocol coverage.
- Added log level and text filters, live refresh, follow-latest, collapsible
  error details, filtered download, and explicit current-log clearing.
- Centralized logging in `gst_worker/logs.py` with UTC timestamps, bounded
  reads, rotation, and structured log API responses.
- Removed obsolete single-language helpers and worker re-exports; tests now import
  domain modules directly. Removed legacy batch-profile migration and log API
  `lines` / local-timestamp compatibility. Explicit batch sizes are preserved.
- Removed array-form `targets.json` support; use the object format containing
  `source_languages` and `target_languages`.
- Added subtitle and language context, elapsed time, and readable retry times
  to worker job logs.

# 2026-08-17
- Classified Gemini daily quota and provider-unavailable failures instead of
  treating every `gst` exit 130 as a subtitle-content retry.
- Added deferred queue retries for `503` responses and a persisted 24-hour
  circuit breaker for daily quota exhaustion.
- Changed translation batches to two levels, `500` and `300`, while retaining
  `gemini-flash-latest` as the default model.

# 2026-07-23
- Fixed the dependency constraint test so compatible Dependabot 3.x update PRs
  do not fail CI when the minimum version changes.

# 2026-07-21
- Migrated dependency management from `requirements.txt` and pip to
  `pyproject.toml`, uv, and a reproducible `uv.lock`.
- Upgraded gemini-srt-translator to 3.6.1.
- Added a manual upstream dependency update script.
- Added daily Dependabot dependency update pull requests.
- Added CI checks for the locked environment, test suite, Python compilation,
  and the upstream `gst` CLI.

# 2026-07-01
- Upgraded gemini-srt-translator to 3.5.9 for improved upstream FFmpeg handling
  and correct interrupted-translation exit codes.

# 2026-06-24
- Upgraded gemini-srt-translator to 3.5.8 for upstream package upgrade fixes.

# 2026-06-23
- Added the Docker sidecar worker and Bazarr post-processing enqueue script.
- Added the web console for status, wanted items, local scans, queue, settings,
  logs, backups, and connection tests.
- Added multi-source and multi-target language support using Bazarr's language
  API.
- Added scheduled backups every 7 days with 30-day retention.
- Added secret masking and connection tests for Bazarr, Gemini, and TMDB.
- Added the Gemini model selector and gemini-srt-translator CLI settings.
- Added gemini-srt-translator 3.5.7 token report support.
- Added recommended translation tuning defaults and a configurable 10-minute
  queue settle window.
- Added optional TMDB translation context and Bazarr scan refreshes.
- Added the optional FFmpeg Docker build flag; FFmpeg is disabled by default
  because Bazarr performs subtitle extraction.
- Added project documentation, MIT license, contribution guide, and security
  notes.
- Split backend helpers into modules for config, subtitle paths, queue, Bazarr
  API, TMDB descriptions, translation execution, backups, and HTTP handling.
- Removed the unused Token Stats setting from the web console and translation
  command.
- Fixed the Docker web port mapping to consistently use `6789:6789`.
