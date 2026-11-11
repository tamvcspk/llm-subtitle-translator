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

**Fallback model**:
An optional second Gemini model that takes over a translation job when the primary model stays unavailable through every delayed retry
or has exhausted its own daily quota.

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
