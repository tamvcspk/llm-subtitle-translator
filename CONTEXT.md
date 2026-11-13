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
The period after every configured model has exhausted its daily Gemini quota
during which new translation jobs and retries are blocked; waiting jobs are
failed rather than automatically resumed. Exhausting only one model's quota
pauses that model alone. The pause follows the models configured now: switching
to a model that still has quota lifts it.

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

**Queue lifecycle**:
The rules that move a translation job through the job queue, including settling,
recovery, retries, quota pauses, and terminal outcomes.

**Glossary**:
Human-owned YAML rules for one series and target language: terms, characters,
and address rules. It always overrides anything the machine infers.

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
and `series` are written to the glossary.

**Locked cue**:
A cue the person edited by hand. Every later retranslation copies it verbatim,
never requests it, and shows it to the model as a reference translation.

**Retranslation**:
A manually requested translation job for a unit, for the whole episode or a cue or
time range, that overwrites the target after keeping the old file as a version.
It runs ahead of automatic jobs. Only the range is requested, but the whole
source is still sent as context.

**Stale translation**:
A translation made with different rules (glossary file or episode corrections),
or with a different story behind it (the facts of an earlier episode changed, or a
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
