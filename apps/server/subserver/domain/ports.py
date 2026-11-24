"""Ports of the worker core, and the named types its storage returns (SPEC ADR-8, ADR-12).

A port is a narrow ``Protocol`` for something the core needs from outside and does not own: Bazarr
(``MediaLibrary``), TMDB (``Metadata``), the model provider (``ProviderFactory``), and the ``Telemetry`` sink.
Adapters implement them; the core only ever sees these types. The application's own SQLite (``store/``) is
not behind a port since P8: the core uses ``Store`` directly and tests run it on ``:memory:``. The record
types are what ``Store`` returns, so column names stay inside it; they live in ``records.py`` and
``library_records.py`` and are re-exported here, so every caller imports them from ``domain.ports``.
``test_core_boundary`` checks that no ``Store`` method returns a ``dict`` or ``sqlite3.Row``. Wiring happens in
``RuntimeContext`` and ``worker.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from subcore.providers import CallLog, Provider

# Re-exported record types: callers import them from here, as they did when they were defined in this file.
from .library_records import *  # noqa: F401,F403
from .records import *  # noqa: F401,F403


class MissingApiKey(RuntimeError):
    """No provider API key is configured."""


class MediaLibraryError(RuntimeError):
    """The media library could not be read or answered something unusable. The message is a sentence for the admin:
    it never holds a URL or a key."""


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
        bundle_hash: str = "", style_hash: str = "", series_id: int | None = None,
    ) -> None:
        """Record one provider call and, as the settings allow, its prompt and answer. ``series_id`` is the series the
        job works for, when it is about one."""


class LinkResolver(Protocol):
    """Turns the link a grounded answer gives for a page into the page's own address (P16, FR-7)."""

    def resolve(self, url: str) -> str | None:
        """The address of the page, or ``None`` when it cannot be told (an error, a timeout, a link that is no page). Never raises."""


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
