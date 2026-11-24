"""The named types ``Store`` returns for the library: series, units, outputs, corrections, glossaries, suggestions, styles
and the cue pairs the feedback loop reads (SPEC ADR-8, ADR-12). Plain frozen dataclasses; ``ports`` re-exports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    display_id: int = 0  # the ``SearchDisplay`` of the search it came from (research), 0 for the rest


@dataclass(frozen=True)
class SearchDisplay:
    """The "Search Suggestions" a grounded search returned: HTML of the provider's own, kept unchanged and shown next to
    what the search gave (Google's terms of Grounding with Google Search), and the queries it ran."""

    id: int
    series_id: int
    target_lang: str
    html: str
    queries: list[str]
    created_at: float


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
