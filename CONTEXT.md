# Subtitle Translation

Translate source subtitles into missing target languages for Bazarr-managed media.

## Language

**Translation job**:
A request to translate one source subtitle into one target language for a media item.
_Avoid_: Batch (a batch is only part of a translation attempt).

**Job queue**:
The collection of translation jobs awaiting execution, running, deferred for a
later attempt, completed, or failed.

**Daily quota pause**:
The period after every configured model has exhausted its daily Gemini quota. Jobs
wait for the reset (`waiting`, reason `quota`); none is failed and new requests are
accepted. Exhausting only one model's quota pauses that model alone, and its jobs move
to the other model. The pause follows the models configured now: switching to a model
that still has quota lifts it.

**Job**:
One translation of one source subtitle into one target language, stored as a row of the
`jobs` table. It has a state (`queued`, `running`, `waiting`, `done`,
`skipped`, `failed`, `cancelled`), an origin (`manual`, `policy`, `retranslate`, ...), a class (interactive, retranslate, auto) and, while
waiting, a reason. A job is only ever failed for its own faults: its source, its glossary,
an invalid output after the strong model, a refused content filter, or too many crashes.

**Extract job** (job kind `extract`):
One job reads the facts of one episode with the memory model (one call). "Extract facts (N episodes)" makes N of them in story
order, so Stop works between episodes and a translation can go in between. It waits for the day's quota like any job.

**Suggest job** (job kind `suggest`):
One job looks for proposals (address rules, terms, characters) in the stored facts of one series for one language, in at most
three calls, each kind stored as soon as it returns. The admin asks for it; a translation that read new facts leaves one behind
(origin `facts`, the lowest class), and at most one is open per series and language.

**Waiting** (job state `waiting`):
A job that cannot run now for a reason outside its own control. The reason is shown:
`quota`, `provider` (key refused, model unknown, breaker open), `context` (earlier
episodes' facts missing), `settle` (source changed a moment ago) or `rate` (provider busy).
A waiting job is not a failed job.

**Breaker**:
The per (provider, model) switch that stops calls to a model after repeated rate-limit or
unavailable errors. It opens after `breaker_threshold` failures in a row, rests for a
growing time, and then lets exactly one trial call through (half-open). Stored in
`provider_health`.

**Lease**:
A running job's claim on the worker, renewed by a heartbeat while the translation runs.
A job whose lease runs out, or that is still marked running when the worker starts, goes
back in line with one more attempt.

**Clean-up** (settings `retention_*`, FR-13):
The daily and on-demand deletion of what the worker made for itself: finished job rows,
stored copies of older subtitle versions, earlier glossary versions and leftover temporary files.
It always starts as a preview (`dry_run`), and never touches a media folder, an unfinished
job, an Ignored decision, the catalog, a current version or the story memory. `0` switches a group off.

**Pruned** (version status):
A subtitle version whose stored copy clean-up deleted. Its line stays in History, but it
cannot be restored or compared. The current version and the first one are never pruned.

**Blocked answer**:
A model answer refused by the provider's content filter. It is repaired and split like
any unusable answer; if it still fails, the job fails with the reason saying so.

**Strong model** (setting `gst_fallback_model`):
The second of two model layers. The default model is a light one that translates
every job; the strong model takes over a job the default cannot finish: a hard
job, an outage that outlasts every delayed retry, or a spent daily quota. Optional;
without it there is one layer.

**Hard job**:
A translation job whose output stays invalid after repair and splitting on the
default model. It moves to the strong model once; if that fails too the job fails.

**Wanted language**:
A target language Bazarr still lists in an item's `missing_subtitles` and that no
sibling subtitle already provides. Only wanted languages are translated.

**Catalog** (table `catalog_items`):
The library as Bazarr lists it (series, movies, episodes, their subtitles and the languages Bazarr
still wants), kept as a cache with an age. The worker pulls it every `catalog_sync_interval` seconds
and on **Sync now**; a read never creates a job. An item is identified by Bazarr's own id, not by a
path, so a rename does not break a waiting job. A read that fails, comes back empty or shrank by more
than `catalog_shrink_guard` is set aside and the cache stays as it was.

**Derived state** (the label of an item and language):
What Library shows, worked out when it is read from three independent things, never stored: the
content (Missing, Translated, Edited, External, Outside profile, with Removed and Stale), the job
(Requested, Running, Waiting with a reason, Failed) and the decision (Ignored). "Missing · Requested"
is a missing language with an open job.

**Request**:
Not a thing of its own: asking for a translation is making a job. The admin asks in Library (one
item, a selection, a season, a series); a request for more than one job shows an estimate and asks
for confirmation. A second request for the same item and language makes no second job.

**Auto-request** (setting `auto_request`, per series override):
The policy that makes jobs by itself, after each accepted read of the catalog, for what Bazarr lists
as missing: only where it is on (everywhere, or for a series), never for an Ignored or Removed item or
one that has a job, within `GST_ONLY_PATHS` and `auto_request_daily_cap`. Off by default. Its jobs have
origin `policy` and are the automatic class, so they never bring back what the admin removed.

**Bootstrap** (setting `auto_request_bootstrap`, `bootstrap.py`):
While a series has nothing approved (no glossary with anything in it, no character card, no suggestion
decided), `auto_request` asks for only its first episodes in story order; the rest stay Missing with the
reason *Waiting for glossary review*. It is a reason shown by Library, not a job state. It ends when the
admin presses **Continue**, or once something is approved and nothing waits in Review. The admin's own
requests are never held back.

**Ignored** (table `unit_decisions`):
The admin's "do not translate this language of this item". Neither a read nor the policy makes a job for
it; the admin's own request refuses it too until it is taken back.

**Removed** (version status):
The admin deleted the target subtitle from the console. The item is Missing again but is not requested
by the policy, only by the admin.

**Queue lifecycle**:
The rules that move a translation job through the job queue, including settling,
recovery, retries, quota pauses, and terminal outcomes.

**Port**:
A narrow interface (`Protocol`, in `subserver/ports.py`) for something the worker core
needs from outside and does not own: Bazarr, TMDB, the model provider. It returns named
immutable types, never a `dict` or a database row. The application's own SQLite
(`Store`) is not behind a port: the core uses it directly, and tests run it on `:memory:`.

**Adapter**:
The code behind a port that touches the outside world (SQLite, the queue directory,
Bazarr's API, the HTTP handler). Core modules never import an adapter; `worker.py`
wires them together.

**Use case**:
One thing the console or a script can ask the worker to do (enqueue, retranslate,
revert, retry, cancel), written once in the core (`ConsoleActions`, `UnitService`) so
the HTTP handler only parses, authenticates and formats.

**Glossary**:
Human-owned rules for one series (or movie) and target language: terms, characters,
and address rules. It always overrides anything the machine infers, and the style too.
It lives in the database and is edited on a form; YAML is only the exchange format
(import and export). Each save keeps what it replaced in the glossary's history.

**Style profile**:
How a translation should sound, as data: axes (formality, tone, swearing, idioms,
dialect, anime honorifics), subtitle limits (characters per second, line length, lines
per cue) and a note. Set in layers, lowest first: the target language, the series'
genre **preset**, the series, an episode, a **character card**; each layer overrides
the one below field by field, and the glossary overrides all of them. The effective
style is in the bundle and in the rules hash (changing it flags translations stale).

**Character card**:
The voice of one character of a series: how they speak, how they address others,
catchphrases, sample lines, and the style axes where they differ from the series. It
is in an episode's prompt where the dialogue names the character.

**Suggestion** (table `suggestions`, `suggestions.py`):
Something a generator proposes for the glossary or the character cards of a series: a `term`, an
`address_rule` or a `character` (with its voice), with the cue and timecode or the URLs that show why. Its
`source` (`facts`, `memory`, `feedback`, `research`) is only a label: every source is stored, decided
and applied the same way. It waits as `pending` until the admin accepts it (optionally after editing it),
rejects it, or `auto_accept_*` applies it. Accepting writes the glossary and flags translations made without
it stale, never retranslates; the same entry is never proposed again once decided (its *identity*: the term's source,
the pair of an address rule, the character's name, however the model words it).
**Review** is the page that lists them, with a badge of the pending ones on the nav.
The first generator (`proposals.py`, `FactsProposer`, with the pure `subcore.suggest`) reads the stored facts of a series:
a pair of characters whose relationship the facts show becomes an `address_rule` per stretch of the story (`from`/`until`
at the cue where it turned), a name in two or more episodes and not in the glossary a `term` (both `facts`), a character in
two or more episodes a `character` with its card (`memory`). It runs after a job extracted facts, or on request, never
fails the job, and does not ask again about what the inbox holds in any status.

**Preset**:
A genre's starting style (sitcom, drama, anime, documentary, kids), stored once and then
the admin's to edit or clone. The Style page suggests one from the series' TMDB genres.

**Few-shot example**:
A line an admin corrected elsewhere in the same series, shown to the model with its fixed
translation when it looks like the lines being translated, within a token budget. It is in
the bundle and its hash, never in the rules hash: it changes with every cue you fix, and
must not make a whole series stale.

**Variant**:
A job (kind `variant`) that translates a whole episode with another style and keeps it as a
candidate beside the current translation. It is compared line by line, and the admin's
verdicts are recorded as `ab` quality signals; nothing goes on disk unless the admin
chooses to.

**Locked term**:
A glossary term whose translation must appear in the output of every cue whose
source contains the term. A violation is repaired; if the model still refuses it
is kept with a warning rather than failing the job.

**Repair**:
A follow-up model call that re-requests only the cues whose answer was missing or
invalid, with a few neighbouring cues as context.

**Translation attempt**:
One execution of translation work for a job: reading the source, calling the
engine, and publishing the finished result. An interrupted attempt keeps nothing
and starts again from the beginning.

**Translation version**:
One published result for a target subtitle path, with its sha256, the input and
context it was made from, and a stored copy. Retranslating or reverting adds a
version instead of replacing history.

**Candidate**:
A translation version that is kept but is not on disk. A job makes one instead of
overwriting when the file appeared while it ran, the subtitle changed under a replace
job, or the source changed. The admin adopts it ("Use this version") to publish it.

**Removed version**:
A tombstone that the admin left by deleting the target subtitle. It keeps the last
content so a revert can bring it back, and an automatic job does not request the
episode again while it is the newest version.

**Publish announcement**:
A translation version written as `publishing` before its file is renamed into place
and confirmed afterwards. A restart settles it: a file that carries the announced
sha256 is confirmed, any other file cancels it (the result becomes a candidate).

**System-made file**:
A target subtitle whose sha256 equals the current translation version for its
path. Any other existing file (hand-edited or foreign) is never overwritten by an
automatic job and is backed up before a retranslate or revert.

**Rate limiter**:
Per-API-key request and token pacing (RPM, TPM, RPD) around every model call.
Rate-limit and overload errors are retried with backoff inside the attempt; only
when they persist does the queue defer the job.

**Unit**:
One episode of a series, or one movie. Registered when the first job for it runs;
identified from Bazarr ids or, failing that, the file name. The Library lists units.

**Correction**:
A person's fix to how a unit is translated. Kinds: `term`, `address`, `note`
(applied by the model on the next retranslation) and `cue_edit` (the person's own
text for one cue, applied at once). Scope `episode` lives in `app.db`; `forward`
and `series` are written to the series' glossary.

**Locked cue**:
A cue the person edited by hand. Every later retranslation copies it verbatim,
never requests it, and shows it to the model as a reference translation.

**Retranslation**:
A manually requested translation job for a unit, for the whole episode or a cue or
time range, that overwrites the target after keeping the old file as a version.
It runs ahead of automatic jobs. Only the range is requested, but the whole
source is still sent as context.

**Stale translation**:
A translation made with different rules (glossary, style or character cards, or episode
corrections), or with a different story behind it (the facts of an earlier episode changed, or a
missing one arrived), than the ones that apply to its episode now. Only ever
flagged; never retranslated automatically.

**Episode facts**:
What the source subtitle of one unit shows about the story: characters, changes in
how two characters stand to each other (with the cue where it shows), terms, and a
summary. Read from the source subtitle only, never from a translation, and stored
per source sha256 and extraction prompt version, so they do not depend on the order
episodes are translated in.

**Story state @N**:
The deterministic fold of the facts of every episode before episode N in story
order: the characters, the latest relationship of each pair, the summaries of the
earlier episodes (the latest few in full, older ones condensed). Never includes a
later episode. Season 0 (specials) sits outside the chain and sees the state after a
configured episode.

**Bundle** (`subcore.bundle`):
Everything one translation reads, as plain data: the cues with their timing, the episode's glossary
(already narrowed to the episode), its address rules placed on cues, the effective style and the voices of the
characters the dialogue names, the corrections and story sections, few-shot examples by value, the cues asked for
and the model parameters. The worker gathers it from the
database (`subserver/gather.py`); the engine renders and runs it; the CLI runs the same file. It holds no key
and no machine path. `bundle_version` says how to read it, and `bundle_hash` changes only when the prompt or a
parameter changes.

**Job spec**:
The bundle a job's latest attempt translated from, kept with the job (table `job_bundles`) and downloaded from the console
(Export job spec). `subcore translate --bundle` runs it again, so a translation can be reproduced outside
the worker.

**Context bundle**:
The story state @N, the unit's own facts and the metadata, as prompt sections placed
after the glossary and the corrections. Its fingerprint (a hash of the facts it was
built from) is stored with the translation; a different fingerprint now means the
translation is stale.

**Prerequisite**:
An earlier episode whose facts must exist before a unit is translated. Its facts are
read on the spot when its subtitle exists.

**Context gap**:
An earlier episode with no source subtitle yet. The job is deferred for up to
`memory_gap_wait_seconds`, then translated anyway and marked `incomplete`, naming the
missing episodes.

**Event**:
One decision of the worker with the job or unit it concerns and a reason an admin can
read: a job skipped because the target already exists, a model chosen, a job escalated or
deferred, a version published. Events answer "why did this episode not run?". They are
rows in the `events` table and one JSON line each in the log, never the place for
subtitle text, keys or passwords.

**Call record**:
The row `llm_calls` keeps for each attempt at a model call: tokens, latency, outcome, and
how the call was made (prompt version, glossary hash, translate, repair or split, part of
the episode, HTTP status, attempt number, cues repaired, estimated cost).

**Prompt body**:
The prompt and the answer of one model call, kept in a daily JSONL file under
`logs/llm/` when `log_prompt_bodies` allows. The database only indexes where it is;
cleanup removes old files and the index then says the body was *rotated*.

**Quality signal**:
A sign of how good a translation was, made by what someone did or what the validator saw,
never by an extra model call: a cue the admin edited (before and after), a revert, a
retranslation request, a problem the validator found in a reply (missing cue, broken tags,
too many lines, a broken locked term), even when a repair fixed it.

**Telemetry**:
Events, call records, quality signals and prompt bodies together. Written best effort on
one background thread: when it cannot keep up or a write fails the record is dropped and
counted, and the job is never delayed or failed by it.

**Setting**:
A value the admin edits in the console, stored in the `settings` table and live as soon as
it is saved. It beats the environment variable of the same meaning, which beats the default.
Settings that describe the process itself (paths, port, the console account) are *system
settings*: they come from the environment at start-up and the console cannot write them.

**API key**:
A credential for a provider or for Bazarr or TMDB, stored in the `api_keys` table with a
provider, a label, a *status* (`ok`, `invalid`, `exhausted`) and the time it was last used.
The environment seeds the keys on the first run only; afterwards the database wins. The API
returns only the last four characters.

**Model provider**:
The service the models run on, one for the whole worker: `gemini`, or `openai_compat`, any server
that speaks the OpenAI chat completions API (OpenRouter, Ollama, LM Studio, vLLM) at a configured
*base URL*. The setting `llm_provider` picks it and takes effect at the next call. A server of your
own needs no key. The provider says what it can do (*capabilities*: token limits, structured
output); when a server refuses structured output the adapter falls back to JSON mode and then to
the prompt alone, and every reply is validated cue by cue either way.

**Task model**:
The model a kind of call runs on: `translate` (`gst_model`, with the strong model as the second
layer), `extract` (`memory_model`), `summarize` (`summarize_model`, else `memory_model`), `research`
(`research_model`). Each falls back to `gst_model`, so one model setting is enough.

**Invalid key**:
A key the provider refused (`AuthError`). It is passed over, the next key takes over, and
Settings and the dashboard warn. Pasting a new key, or **Test key** on one that works, makes
it `ok` again and the next call uses it.
