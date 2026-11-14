"""Ports of the worker core (SPEC ADR-8).

A port is a narrow ``Protocol`` that the core defines for something it needs from outside: storage, the
queue, Bazarr, TMDB, the provider. Adapters (``store.py`` for SQLite today) implement it; the core only
ever sees these types. Ports return named, immutable types, never ``dict`` or ``sqlite3.Row``, so column
names exist only inside the adapter. Wiring happens in ``RuntimeContext`` and ``worker.py``.

While ``Store`` still implements several ports at once, a port's method names carry the port's prefix
(``auth_account``) so they do not collide there.
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


@dataclass(frozen=True)
class LlmCall:
    """One recorded provider call (NFR-7). ``at`` is epoch seconds."""

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


@dataclass(frozen=True)
class TokenTotals:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


CURRENT = "current"
SUPERSEDED = "superseded"


@dataclass(frozen=True)
class TranslationVersion:
    """One stored version of a target subtitle file (FR-8.1). ``status`` is ``current`` or ``superseded``;
    ``trigger`` is auto | retranslate | revert | external | cue_edit. ``missing_units`` lists the earlier
    units that had no usable facts when this was made (``context_complete`` is then False)."""

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
class AuthRepo(Protocol):
    """The single console account and the key its sessions are signed with."""

    def auth_account(self) -> Account | None:
        """The account, or None before the first run has created one."""

    def save_auth_account(self, user: str, password_hash: str) -> None:
        """Create or overwrite the account. Every write also rotates the session secret, ending all sessions."""

    def auth_session_secret(self) -> str | None:
        """Hex session secret; None without an account. Stable until the account is next written."""


@runtime_checkable
class UsageLog(Protocol):
    """Record of every provider call, for the call history and token totals."""

    def record_llm_call(
        self,
        job_id: str,
        *,
        provider: str,
        model: str,
        task: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        outcome: str,
        at: float | None = None,
    ) -> None:
        """Append one call; ``at`` defaults to now."""

    def llm_calls(self, job_id: str | None = None) -> list[LlmCall]:
        """Calls in the order recorded, for one job or (without ``job_id``) all of them."""

    def token_totals(self, since: float = 0.0) -> TokenTotals:
        """Number of calls and token sums for calls at or after ``since`` (epoch seconds)."""


@runtime_checkable
class VersionRepo(Protocol):
    """History of every target subtitle file, keyed by output path, plus the result cache (NFR-3)."""

    def current_version(self, output_path: str) -> TranslationVersion | None:
        """The version whose ``status`` is ``current``, if any."""

    def versions(self, output_path: str) -> list[TranslationVersion]:
        """All versions of one file, oldest first."""

    def get_version(self, output_path: str, version: int) -> TranslationVersion | None:
        """One version by number."""

    def next_version(self, output_path: str) -> int:
        """Number the next version of this file should get (1 for a new file)."""

    def add_version(
        self,
        output_path: str,
        *,
        version: int,
        stored_path: str,
        sha256: str,
        trigger: str,
        make_current: bool = True,
        source_path: str = "",
        source_sha256: str = "",
        target_lang: str = "",
        context_hash: str = "",
        parent_version: int | None = None,
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        unit_id: int | None = None,
        glossary_hash: str = "",
        context_complete: bool = True,
        memory_hash: str = "",
        missing_units: Iterable[str] = (),
    ) -> None:
        """Record a version. With ``make_current`` it atomically supersedes the file's previous current one."""

    def cached_versions(self, source_sha256: str, target_lang: str, context_hash: str) -> list[TranslationVersion]:
        """Auto and retranslate versions made from the same input and context, newest first."""


@runtime_checkable
class MemoryRepo(Protocol):
    """Story memory (FR-5): facts per unit, the cache of compressed arcs, and the snapshots of context bundles."""

    def get_facts(
        self, unit_id: int, source_sha256: str, prompt_version: str, *, touch: bool = False
    ) -> StoredFacts | None:
        """Facts for exactly this source and prompt version. ``touch`` marks them as the newest in use."""

    def save_facts(
        self,
        unit_id: int,
        source_sha256: str,
        prompt_version: str,
        *,
        model: str,
        facts: Facts,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        """Store facts, replacing any for the same unit, source and prompt version; they become the newest."""

    def latest_facts(self, unit_ids: Iterable[int], prompt_version: str) -> dict[int, StoredFacts]:
        """For each unit with any, the facts most recently saved or used under ``prompt_version``."""

    def get_summary(self, input_hash: str) -> str | None:
        """A compressed arc by the hash of the entries it summarizes."""

    def put_summary(self, series_id: int, up_to_unit: int, input_hash: str, text: str) -> None:
        """Cache a compressed arc."""

    def put_snapshot(self, memory_hash: str, sections: Iterable[tuple[str, str]]) -> None:
        """Keep the (title, body) sections of the context a fingerprint stands for."""

    def get_snapshot(self, memory_hash: str) -> list[tuple[str, str]] | None:
        """The sections kept for a fingerprint, or None."""


@runtime_checkable
class CatalogRepo(Protocol):
    """The library: series, units (episodes and movies), their target files and episode corrections."""

    def upsert_series(self, key: str, *, title: str = "", bazarr_id: str = "") -> int:
        """Id of the series with this key, created if new. A non-blank ``title``/``bazarr_id`` refreshes it."""

    def get_series(self, series_id: int) -> Series | None:
        """One series by id."""

    def list_series(self) -> list[Series]:
        """All series by title (case-insensitive), then id."""

    def set_series_memory(self, series_id: int, *, memory: str | None = None, specials_after: str | None = None) -> None:
        """Set the story-memory policy and/or the season-0 anchor; a field left as None stays as it is."""

    def upsert_unit(
        self,
        *,
        series_id: int | None = None,
        movie_key: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        title: str = "",
        media_type: str = "",
        bazarr_id: str = "",
        video_path: str = "",
        source_path: str = "",
        source_code: str = "",
        source_language: str = "",
    ) -> int:
        """Id of the unit, created if new. Identity is (series, season, episode) or the movie key (exactly one
        of ``series_id`` and ``movie_key``). A blank field never erases a known value."""

    def get_unit(self, unit_id: int) -> Unit | None:
        """One unit by id."""

    def find_unit(self, series_id: int, season: int, episode: int) -> Unit | None:
        """The episode of a series, if known."""

    def list_units(self, series_id: int | None = None, *, movies: bool = False) -> list[Unit]:
        """Episodes of a series by season and episode, or (``movies``) all movies by title."""

    def set_output(self, unit_id: int, target_lang: str, output_path: str, target_language: str = "") -> None:
        """Record a unit's target file for a language; a blank ``target_language`` keeps the known one."""

    def outputs(self, unit_id: int) -> list[Output]:
        """The target files of a unit, by language."""

    def add_correction(self, unit_id: int, target_lang: str, kind: str, payload: dict[str, Any]) -> int:
        """Store an active correction; returns its id."""

    def get_correction(self, correction_id: int) -> Correction | None:
        """One correction by id, active or not."""

    def corrections(self, unit_id: int, target_lang: str, *, active_only: bool = True) -> list[Correction]:
        """Corrections of a unit in a language, oldest first."""

    def deactivate_correction(self, correction_id: int) -> bool:
        """Turn an active correction off; False when it was not active (or does not exist)."""


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


@runtime_checkable
class JobQueue(Protocol):
    """How the core submits and manages translation jobs. The queue's own life cycle (claiming a job, deferring
    it, quota pauses, recovery) belongs to the adapter and the worker loop, not to the core."""

    def enqueue(self, base_job: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]:
        """Queue one automatic job per enabled target language that is still missing; returns what was queued.
        Raises when the daily quota pause is active."""

    def enqueue_retranslation(self, job: dict[str, Any]) -> str:
        """Queue one explicit retranslation ahead of automatic work; returns its job id. Raises ``ValueError``
        when one for the same target is already waiting."""

    def retry(self, job_id: str) -> bool:
        """Put a failed job back in line; False when there is no such failed job."""

    def cancel(self, job_id: str) -> bool:
        """Remove a failed job for good; False when there is no such job."""

    def delete(self, state: str, job_id: str) -> bool:
        """Remove a job that is in ``state``; False when there is none."""
