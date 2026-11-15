"""Ports of the worker core, and the named types its storage returns (SPEC ADR-8, ADR-12).

A port is a narrow ``Protocol`` for something the core needs from outside and does not own: Bazarr
(``MediaLibrary``), TMDB (``Metadata``), the model provider (``ProviderFactory``), and the ``Telemetry`` sink.
Adapters implement them; the core only ever sees these types. The application's own SQLite (``store.py``) is
not behind a port since P8: the core uses ``Store`` directly and tests run it on ``:memory:``. The record
types below are what ``Store`` returns, so column names stay inside it; ``test_core_boundary`` checks that no
``Store`` method returns a ``dict`` or ``sqlite3.Row``. Wiring happens in ``RuntimeContext`` and ``worker.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from subcore.memory import Facts
from subcore.providers import CallLog, Provider


class MissingApiKey(RuntimeError):
    """No provider API key is configured."""


@dataclass(frozen=True)
class Account:
    """The one console account. The hash stays out of ``repr`` so a traceback or log line cannot leak it."""

    user: str
    password_hash: str = field(repr=False)


KEY_OK, KEY_INVALID, KEY_EXHAUSTED = "ok", "invalid", "exhausted"
KEY_STATUSES = (KEY_OK, KEY_INVALID, KEY_EXHAUSTED)


@dataclass(frozen=True)
class ApiKey:
    """One stored API key (FR-20.2). The value stays out of ``repr`` so a traceback or log line cannot leak it."""

    id: int
    provider: str  # gemini | bazarr | tmdb (and the providers P7 adds)
    label: str
    value: str = field(repr=False)
    status: str = KEY_OK
    last_used_at: float | None = None
    updated_at: float = 0.0


@dataclass(frozen=True)
class SettingRow:
    """One stored setting: ``value`` is already decoded from the JSON the table keeps."""

    key: str
    value: Any
    updated_at: float = 0.0


@dataclass(frozen=True)
class LlmCall:
    """One recorded provider call (NFR-7, FR-19.1). ``at`` is epoch seconds. The fields after ``at`` describe
    how the call was made: ``kind`` is translate | repair | split, ``batch`` the part of the episode, ``retries``
    the attempt number inside one request, ``repairs`` the cues a repair call was asked for, ``cost_est`` the
    estimated price in the currency of the configured per-million-token prices (0 when none are set).
    The hashes are empty until the stage that computes them exists (bundle: P11, style: P12)."""

    id: int
    job_id: str
    provider: str
    model: str
    task: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    outcome: str
    at: float
    kind: str = ""
    prompt_version: str = ""
    bundle_hash: str = ""
    style_hash: str = ""
    glossary_hash: str = ""
    batch: int = 0
    http_status: int | None = None
    finish_reason: str = ""
    retries: int = 0
    repairs: int = 0
    cost_est: float = 0.0


@dataclass(frozen=True)
class EventRecord:
    """One decision of the system (FR-19.1). ``kind`` is a dotted name (``job.skipped``); ``reason`` says why in a
    sentence an admin can read; ``data`` is the rest, already stripped of secrets. ``at`` is epoch seconds."""

    id: int
    at: float
    level: str
    kind: str
    job_id: str = ""
    unit_id: int | None = None
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualitySignal:
    """What an admin or the validator did to a cue (FR-19.4): ``kind`` is cue_edit | revert | retranslate |
    validate | ab. ``before``/``after`` are the texts either side; ``meta`` carries the rest."""

    id: int
    at: float
    kind: str
    unit_id: int | None = None
    version: int | None = None
    cue: int | None = None
    before: str = ""
    after: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BodyRef:
    """Where the prompt and answer of one call are kept (FR-19.2). ``file`` is the segment name without its
    ``.jsonl``/``.jsonl.gz`` ending; ``rotated`` is True once cleanup removed the file (the index outlives it)."""

    call_id: int
    file: str
    offset: int
    length: int
    rotated: bool = False


@dataclass(frozen=True)
class TokenTotals:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


CURRENT = "current"
SUPERSEDED = "superseded"
CANDIDATE = "candidate"  # a result kept but never put on disk until the admin adopts it (FR-16.3, FR-16.4)
REMOVED = "removed"  # tombstone: the admin deleted the target; the content stays for revert (FR-16.6)
PRUNED = "pruned"  # a stored copy removed by retention; the history line stays (FR-13.3, P9)


@dataclass(frozen=True)
class TranslationVersion:
    """One stored version of a target subtitle file (FR-8.1, FR-16.1). ``status`` is current | superseded |
    candidate | removed | pruned; ``trigger`` is auto | retranslate | revert | external | cue_edit | remove;
    ``origin`` is machine | edit | revert | external | import. ``publishing`` marks a version whose file write has
    been announced but not confirmed yet (FR-16.5). ``missing_units`` lists the earlier units that had no usable
    facts when this was made (``context_complete`` is then False)."""

    output_path: str
    version: int
    stored_path: str
    sha256: str
    trigger: str
    status: str
    created_at: float
    source_path: str = ""
    source_sha256: str = ""
    target_lang: str = ""
    context_hash: str = ""
    parent_version: int | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    unit_id: int | None = None
    glossary_hash: str = ""
    context_complete: bool = True
    memory_hash: str = ""
    missing_units: tuple[str, ...] = ()
    origin: str = ""
    publishing: bool = False
    edited_at: float = 0.0
    job_id: str = ""


@dataclass(frozen=True)
class StoredFacts:
    """Story facts extracted from one source subtitle (FR-5.1). Identified by unit, source hash and
    extraction prompt version; ``created_at`` is when they were saved or last used (``touch``)."""

    unit_id: int
    source_sha256: str
    prompt_version: str
    model: str
    facts: Facts
    facts_hash: str
    tokens_in: int
    tokens_out: int
    created_at: float


@dataclass(frozen=True)
class Series:
    """A TV series the worker has met. ``memory`` is '' (follow the global setting), 'on' or 'off';
    ``specials_after`` is an ``S02E05`` anchor for season 0; ``stale_policy`` is flag | ignore."""

    id: int
    key: str
    title: str = ""
    bazarr_id: str = ""
    stale_policy: str = "flag"
    memory: str = ""
    specials_after: str = ""
    created_at: float = 0.0


@dataclass(frozen=True)
class Unit:
    """One episode (``series_id``, ``season``, ``episode``) or one movie (``movie_key``) (FR-10)."""

    id: int
    series_id: int | None = None
    movie_key: str | None = None
    season: int | None = None
    episode: int | None = None
    title: str = ""
    media_type: str = ""
    bazarr_id: str = ""
    video_path: str = ""
    source_path: str = ""
    source_code: str = ""
    source_language: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass(frozen=True)
class Output:
    """The target subtitle file of a unit in one language."""

    unit_id: int
    target_lang: str
    output_path: str
    target_language: str = ""


@dataclass(frozen=True)
class Correction:
    """An episode-scope correction (FR-11.2): kind is term | address | note | cue_edit; ``payload`` is
    the kind's own fields. ``active`` is False once it was removed or superseded."""

    id: int
    unit_id: int
    target_lang: str
    kind: str
    payload: dict[str, Any]
    created_at: float = 0.0
    active: bool = True


@runtime_checkable
class Telemetry(Protocol):
    """What the core uses to leave a trace of its decisions. Best effort: it never raises and never blocks
    the job (NFR-11). ``kind`` of an event is a dotted name; ``reason`` is a sentence for the admin."""

    def event(
        self, kind: str, *, level: str = "INFO", job_id: str = "", unit_id: int | None = None, reason: str = "",
        **data: Any,
    ) -> None:
        """Record a decision."""

    def signal(
        self, kind: str, *, unit_id: int | None = None, version: int | None = None, cue: int | None = None,
        before: str = "", after: str = "", **meta: Any,
    ) -> None:
        """Record a quality signal."""

    def llm_call(
        self, job_id: str, log: CallLog, *, prompt_version: str = "", glossary_hash: str = "",
        bundle_hash: str = "", style_hash: str = "",
    ) -> None:
        """Record one provider call and, as the settings allow, its prompt and answer."""


class ProviderFactory(Protocol):
    """Builds the model provider for one attempt from the worker settings (FR-9)."""

    def __call__(
        self,
        settings: dict[str, Any],
        model: str,
        *,
        deadline: float | None = None,
        on_call: Callable[[CallLog], None] | None = None,
    ) -> Provider:
        """A provider for ``model`` that stops at the monotonic ``deadline`` and reports each call to
        ``on_call``. Raises :class:`MissingApiKey` when the settings hold no key."""


@dataclass(frozen=True)
class MediaDetails:
    """What the media library knows about one item, to identify it (FR-10). Empty fields are unknown."""

    series_title: str = ""
    tvdb_id: str = ""
    season: int | None = None
    episode: int | None = None
    episode_title: str = ""
    movie_title: str = ""


@dataclass(frozen=True)
class EpisodeListing:
    """One episode of a series as the media library lists it. ``source_path`` is empty without a source subtitle."""

    season: int
    episode: int
    title: str = ""
    bazarr_id: str = ""
    video_path: str = ""
    source_path: str = ""


@runtime_checkable
class MediaLibrary(Protocol):
    """The media manager (Bazarr): what an item is and which episodes a series has."""

    def describe(self, media_type: str, series_id: str, media_id: str) -> MediaDetails | None:
        """Details of an episode or movie; None when the library cannot say (no access, unknown type or ids).
        Raises when it cannot be reached."""

    def series_episodes(self, series_id: str, source_code: str) -> list[EpisodeListing]:
        """Every episode of the series, each with its source subtitle in ``source_code`` if it has one.
        Empty when the library cannot be asked; raises when it cannot be reached."""

    def target_still_missing(self, job: dict[str, Any]) -> bool | None:
        """Whether the library still lists the job's target language as missing for its item. False: the
        language is there (or not wanted), spend no model call. None: the library cannot say."""

    def refresh(self, job: dict[str, Any]) -> None:
        """Ask the library to rescan the item's subtitles. Raises when it cannot be reached."""


@runtime_checkable
class Metadata(Protocol):
    """Descriptive text about the item of a job (TMDB), prepended to the prompt as context. Empty when unknown."""

    def description(self, job: dict[str, Any]) -> str:
        """The description for the job's item; ``""`` when there is no key, no match, or the lookup failed."""


@dataclass(frozen=True)
class HeldJob:
    """A job waiting for the admin's decision (FR-12.4), or one that was refused. ``created_at`` is when the
    script queued it, ``held_at`` when the worker received and set it aside; ``cue_count`` is the cues in the
    source subtitle (0 when it cannot be read). ``series_id`` is Bazarr's, empty for a movie."""

    job_id: str
    subtitle_path: str
    source_code: str
    target_code: str
    video_path: str = ""
    source_language: str = ""
    target_language: str = ""
    series_id: str = ""
    media_type: str = ""
    created_at: float = 0.0
    held_at: float = 0.0
    cue_count: int = 0
