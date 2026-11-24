"""Answers of the Review inbox (``api/suggestion_api.py``, P13): what a generator proposed, and what accepting, rejecting,
undoing and asking for more returns."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from . import Answer, maybe
from .library import GlossaryAddress, GlossaryCharacter, GlossaryTerm, Impact
from .style import CharacterCard

SuggestionKind = Literal["term", "address_rule", "character"]
SuggestionSource = Literal["facts", "memory", "feedback", "research"]
SuggestionStatus = Literal["pending", "accepted", "rejected"]


class AddressStagePayload(Answer):
    """One time-windowed stage inside a multi-stage pair ``address_rule`` proposal (P18f)."""

    a_to_b: str = maybe()
    b_to_a: str = maybe()
    from_: str = Field(default=None, alias="from")
    until: str = maybe()
    note: str = maybe()


class AddressProposalPayload(GlossaryAddress):
    """A pair-level ``address_rule`` proposal: flat for one stage, or carrying ``stages`` when a sturdy change point
    was confirmed (P18f). ``change_from`` marks a change proposal for a pair the glossary already rules."""

    stages: list[AddressStagePayload] = maybe()
    change_from: str = maybe()


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
    payload: GlossaryTerm | AddressProposalPayload | CharacterPayload
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
    search_display_id: int | None
    """The Search Suggestions of the web search a research suggestion came from (``GET /api/suggestions/search/{id}``);
    null for the other sources."""


class SearchSuggestions(Answer):
    """``GET /api/suggestions/search/{id}``: what Google's grounding returned to be shown next to the result of a search."""

    id: int
    series_id: int
    lang: str
    html: str
    """Google's own HTML, unchanged: shown as it is, in a sandboxed frame, never edited (its terms)."""
    queries: list[str]
    created_at: float


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


class ResearchBudget(Answer):
    cap: int
    """``research_max_calls_per_series``; ``0`` is no cap."""
    used: int
    """Calls of the research task this series has made over its whole life."""
    after: int
    """``used`` plus the calls this run would make."""


class ResearchEstimate(Answer):
    """``POST /api/suggestions/research`` with ``estimate_only``: what a run would look up and spend. No model call."""

    ok: bool
    estimate_only: bool
    terms: int
    """Terms two or more episodes share that the glossary and the inbox do not have: the ``N`` of the button."""
    asked: int
    """How many of them one run asks about (the most widespread first)."""
    calls: int
    """Model calls: the search and the one that reads its answer (an estimate: a search may run several queries)."""
    search_model: str
    read_model: str
    budget: ResearchBudget
    auto_accept: bool
    """What ``auto_accept_terms`` says now: the default of the tick in the box."""
    blocked: Literal["", "no_web_search", "budget", "no_terms"]
    """Why a run cannot be made now; empty when it can."""
    reason: str
    calls_left_today: int | None
    paused_until: float | None
    minutes: float | None
    finish_at: float | None


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
