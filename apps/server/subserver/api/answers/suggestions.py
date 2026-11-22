"""Answers of the Review inbox (``api/suggestion_api.py``, P13): what a generator proposed, and what accepting, rejecting,
undoing and asking for more returns."""
from __future__ import annotations

from typing import Literal

from . import Answer, maybe
from .library import GlossaryAddress, GlossaryCharacter, GlossaryTerm, Impact
from .style import CharacterCard

SuggestionKind = Literal["term", "address_rule", "character"]
SuggestionSource = Literal["facts", "memory", "feedback", "research"]
SuggestionStatus = Literal["pending", "accepted", "rejected"]


class CharacterPayload(GlossaryCharacter):
    """A character a generator proposes: its Name (glossary entry) and, optionally, its Voice (the card)."""

    card: CharacterCard = maybe()


class EvidenceItem(Answer):
    """What shows why: a cue and its timecode, or a line a person can check."""

    text: str = maybe()
    timecode: str = maybe()
    episode: str = maybe()
    unit_id: int = maybe()
    cue: int = maybe()
    url: str = maybe()
    note: str = maybe()


class Suggestion(Answer):
    id: int
    series_id: int
    series: str
    lang: str
    kind: SuggestionKind
    source: SuggestionSource
    payload: GlossaryTerm | GlossaryAddress | CharacterPayload
    """The glossary entry it would add (a term, an address rule or a character)."""
    evidence: list[EvidenceItem | str]
    sources: list[str]
    """URLs of a research answer."""
    status: SuggestionStatus
    auto_accepted: bool
    decided_at: float
    """Unix seconds; ``0`` while pending."""
    created_at: float
    undoable: bool
    """An accepted suggestion that changed the glossary can be undone."""
    edited: bool
    """Accepted after the admin changed it."""


class SuggestionCounts(Answer):
    pending: int
    accepted: int
    rejected: int


class PendingOfSeries(Answer):
    series_id: int
    series: str
    pending: int


class SuggestionSummary(Answer):
    """``GET /api/suggestions/summary``: what the badge on the nav shows."""

    counts: SuggestionCounts
    pending: int
    """The pending total."""
    series: list[PendingOfSeries]


class SuggestionList(SuggestionSummary):
    items: list[Suggestion]


class SuggestionDecision(Impact):
    """An accept or an undo, or a dry run of one (``saved: false``): what it flags stale. Nothing is retranslated."""

    ok: bool
    saved: bool
    unchanged: bool = maybe()
    suggestion: Suggestion = maybe()


class SuggestionRejected(Answer):
    ok: bool
    suggestion: Suggestion


class BulkFailure(Answer):
    id: int
    error: str


class BulkAnswer(Answer):
    """Accept every pending suggestion of one series; a dry run only counts."""

    ok: bool
    saved: bool
    count: int
    stale_now: int = maybe()
    stale_after: int = maybe()
    would_stale: int
    failed: list[BulkFailure] = maybe()
    series: str = maybe()


class GenerateQueued(Answer):
    """``POST /api/suggestions/generate``: the ``suggest`` job (an open one for the same series and language is not made twice)."""

    ok: bool
    job_ids: list[str]
    queued: int
    merged: int


class GenerateEstimate(Answer):
    ok: bool
    estimate_only: bool
    episodes: int
    address_rules: int
    terms: int
    characters: int
    calls: int


class ProposeReport(Answer):
    added: int
    auto_accepted: int
    pending_already: int
    """The same content is waiting in the inbox."""
    accepted_before: int
    rejected_before: int
    """The admin said no to exactly this: it is not proposed again."""
    in_glossary: int
    """The glossary already says it."""
    invalid: list[str]
    failed_to_apply: list[str]


class FromEditsAnswer(Answer):
    """``POST /api/suggestions/from-edits``: what the admin's corrections of a series suggested. No model call."""

    cues: int
    without_before: int
    patterns: int
    proposed: int
    unnamed: int
    report: ProposeReport | None
