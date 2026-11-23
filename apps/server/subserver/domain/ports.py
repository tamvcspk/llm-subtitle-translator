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


class MediaLibraryError(RuntimeError):
    """The media library could not be read or answered something unusable. The message is a sentence for the admin:
    it never holds a URL or a key."""


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
class JobBundle:
    """The bundle a job's latest translation attempt used (P11, FR-21.2): what "Xuất job spec" hands out."""

    job_id: str
    bundle_hash: str
    bundle_json: str


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
class CallDay:
    """The model calls of one UTC day, one row per (model, prompt version, task, kind, outcome): what ``metrics_daily``
    is rolled up from (FR-19.5)."""

    day: str
    model: str
    prompt_version: str
    task: str
    kind: str
    outcome: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost: float
    latency_ms: int
    repairs: int


@dataclass(frozen=True)
class JobPrompt:
    """The prompt version a job's model calls used (the first call that names one)."""

    job_id: str
    prompt_version: str


@dataclass(frozen=True)
class MetricRow:
    """One figure of ``metrics_daily``: a count or a sum for a UTC day, split by ``labels`` (model, prompt version...)."""

    day: str
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float


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
class Reclaimed:
    """What one cleanup group removed, or would remove in a dry run (FR-13.4): files, their bytes, database rows."""

    files: int = 0
    bytes: int = 0
    rows: int = 0

    def __add__(self, other: "Reclaimed") -> "Reclaimed":
        return Reclaimed(self.files + other.files, self.bytes + other.bytes, self.rows + other.rows)


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
    # How the version was made when that is not in its other fields (P12): ``range`` of a retranslated range
    # (FR-11.3.6) and ``variant`` (label, preset, style) of a variant (FR-18.8). Empty for an ordinary one.
    params: dict[str, Any] = field(default_factory=dict)


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
    ``specials_after`` is an ``S02E05`` anchor for season 0; ``stale_policy`` is flag | ignore;
    ``auto_request`` is '' (follow the global ``auto_request`` setting), 'on' or 'off' (FR-15.4)."""

    id: int
    key: str
    title: str = ""
    bazarr_id: str = ""
    stale_policy: str = "flag"
    memory: str = ""
    specials_after: str = ""
    created_at: float = 0.0
    auto_request: str = ""
    bootstrap_released: bool = False  # the admin pressed Continue: ``auto_request`` is no longer held back (FR-15.12)


@dataclass(frozen=True)
class SeriesSeed:
    """What a series has had approved so far (FR-15.12): whether it has a glossary with anything in it, how many
    character cards, and how many suggestions wait and were decided."""

    glossary: bool = False
    cards: int = 0
    pending: int = 0
    decided: int = 0


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


@dataclass(frozen=True)
class StoredGlossary:
    """The glossary of one owner (a series or the movies folder of a title) in one language (FR-18.10, ADR-14).
    ``doc`` is the YAML schema as a mapping; ``rules_hash`` is the hash of that document; ``version`` counts saves."""

    owner_kind: str
    owner_key: str
    lang: str
    doc: dict[str, Any]
    rules_hash: str = ""
    version: int = 1
    updated_at: float = 0.0


@dataclass(frozen=True)
class GlossaryRevision:
    """An earlier saved state of a glossary: what a save replaced (the history that stands in for the old backups)."""

    version: int
    doc: dict[str, Any]
    at: float = 0.0
    note: str = ""  # who changed it: console | import | correction | restore


SUGGESTION_PENDING, SUGGESTION_ACCEPTED, SUGGESTION_REJECTED = "pending", "accepted", "rejected"
SUGGESTION_STATUSES = (SUGGESTION_PENDING, SUGGESTION_ACCEPTED, SUGGESTION_REJECTED)


@dataclass(frozen=True)
class Suggestion:
    """One thing a generator proposes for the glossary or the character cards of a series (FR-4.4, FR-11.1.5).

    ``source`` says who proposed it (``facts`` | ``memory`` | ``feedback`` | ``research``), ``kind`` what it is
    (``term`` | ``address_rule`` | ``character``). ``fingerprint`` is the hash of its normalised content: the same
    content is never stored twice for a series and language, so a rejected one is never proposed again. ``applied``
    is what accepting changed (the entries it replaced and the ones it wrote) so it can be undone."""

    id: int
    series_id: int
    target_lang: str
    kind: str
    source: str
    fingerprint: str
    payload: dict[str, Any]
    evidence: list[Any] = field(default_factory=list)  # cues with timecodes, or text a person can check
    sources: list[str] = field(default_factory=list)  # URLs of a research answer
    auto_accepted: bool = False
    status: str = SUGGESTION_PENDING
    applied: dict[str, Any] = field(default_factory=dict)
    decided_at: float = 0.0
    created_at: float = 0.0
    identity: str = ""  # what the glossary keys the entry by (``suggestion_keys``); a decision holds for it, however worded


@dataclass(frozen=True)
class SuggestionCount:
    status: str
    series_id: int
    count: int


@dataclass(frozen=True)
class StyleRow:
    """One stored style document. ``scope`` is preset | series | unit; ``scope_id`` names the preset, the owner
    (``series/<key>``) or the unit id."""

    scope: str
    scope_id: str
    doc: dict[str, Any]
    version: int = 1
    updated_at: float = 0.0


@dataclass(frozen=True)
class CardRow:
    """One stored character card of an owner."""

    owner_kind: str
    owner_key: str
    name: str
    doc: dict[str, Any]
    version: int = 1
    updated_at: float = 0.0


@dataclass(frozen=True)
class CueEdit:
    """A cue an admin corrected: the source line and the text it was fixed to (a few-shot candidate, FR-18.5)."""

    unit_id: int
    cue: int
    source_text: str
    text: str


@dataclass(frozen=True)
class CuePair:
    """A cue an admin corrected, with where it is: the machine's text (``before``, empty when it was not kept), the source
    line and the admin's text. What the feedback loop and the golden set are made of (FR-19.6)."""

    unit_id: int
    season: int | None
    episode: int | None
    cue: int
    source: str
    before: str
    after: str


@dataclass(frozen=True)
class CueEditCount:
    """How many cues of one series the admin corrected in one language, whose correction still stands: the size of that
    series' golden set (Insights shows it next to the language)."""

    series_id: int
    lang: str
    edits: int


@dataclass(frozen=True)
class JobOutcomes:
    """How the jobs that finished in a window ended."""

    done: int
    failed: int


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


KIND_SERIES, KIND_MOVIE, KIND_EPISODE = "series", "movie", "episode"


@dataclass(frozen=True)
class CatalogSubtitle:
    """One subtitle the library lists for an item. ``path`` is empty for an embedded track, which the worker
    cannot read; ``code`` is the two-letter language code the library uses."""

    code: str
    path: str = ""
    forced: bool = False
    hi: bool = False


@dataclass(frozen=True)
class CatalogEntry:
    """One series, movie or episode of the library as the media manager lists it (FR-14.1, ADR-10).

    Identity is the library's own id (``kind`` + ``bazarr_id``), never a path (FR-14.3). ``series_key`` is the key
    the worker's series (or movie) carries, so a catalog item and a unit meet. An episode names its series by
    ``parent_bazarr_id``. ``missing`` lists the language codes the library says it still wants. The fields from
    ``id`` on are filled when the entry was read back from the table."""

    kind: str
    bazarr_id: str
    series_key: str = ""
    parent_bazarr_id: str = ""
    season: int | None = None
    episode: int | None = None
    title: str = ""
    video_path: str = ""
    tvdb_id: str = ""
    subtitles: tuple[CatalogSubtitle, ...] = ()
    missing: tuple[str, ...] = ()
    id: int = 0
    parent_id: int | None = None
    synced_at: float = 0.0
    gone_at: float | None = None


@dataclass(frozen=True)
class CatalogState:
    """How the last attempts to read the library went (FR-14.2). ``synced_at`` is the last time a full, plausible
    answer was applied (0 = never); ``status`` is ``ok`` or ``kept`` (the last answer was set aside and the cache
    stayed as it was, ``warning`` says why); ``items`` is what the last applied answer held."""

    synced_at: float = 0.0
    attempted_at: float = 0.0
    status: str = ""
    warning: str = ""
    items: int = 0


@dataclass(frozen=True)
class CatalogApplied:
    """What applying one answer changed: new items, items seen again, items no longer listed."""

    added: int = 0
    updated: int = 0
    gone: int = 0


@runtime_checkable
class MediaLibrary(Protocol):
    """The media manager (Bazarr): what an item is and which episodes a series has."""

    def catalog(self) -> Iterable[CatalogEntry]:
        """Every series, movie and episode the library lists, with their subtitles and missing languages (FR-14.1).
        Read in pages by the adapter; raises when the library cannot be reached or answers badly, so the caller
        can keep what it already has (FR-14.2)."""

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

    def genres(self, job: dict[str, Any]) -> tuple[str, ...]:
        """The genre names of the job's item (English, as TMDB spells them), to suggest a style preset (FR-18.4);
        empty when there is no key, no match, or the lookup failed."""
