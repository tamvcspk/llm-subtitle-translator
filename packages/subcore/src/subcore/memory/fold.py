"""Story state @N: a deterministic fold of the facts of every unit before N (FR-5.2, FR-5.8, ADR-6).

``fold`` is plain Python over immutable facts, so the same facts always give the same state, whatever
order the episodes were translated in. Only the compression of *old* summaries may call a model, and
that goes through a caller-supplied ``compress`` callable (which the worker caches).

Nothing here knows about queues, files or Bazarr.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..providers.base import BadOutput
from .facts import INTIMACY_MAX, MIN_CONFIDENCE, Character, Facts

__all__ = [
    "DEFAULT_BUDGET_TOKENS",
    "DEFAULT_RECENT_UNITS",
    "Relationship",
    "StoryContext",
    "StoryState",
    "UnitFacts",
    "build_context",
    "entries_hash",
    "fingerprint",
    "fold",
    "render_episode",
]

DEFAULT_BUDGET_TOKENS = 8000  # FR-5.8
DEFAULT_RECENT_UNITS = 3  # FR-5.8
_CHARS_PER_TOKEN = 4
_MAX_FOLDED_CHARACTERS = 40
_MAX_FOLDED_RELATIONSHIPS = 60
_OLD_SUMMARY_CHARS = 160
# A later event below _OVERTURN_BELOW does not replace one at or above _CONFIDENT for the same pair.
_OVERTURN_BELOW = 0.6
_CONFIDENT = 0.9

Compressor = Callable[[list[tuple[str, str]]], str]


@dataclass(frozen=True)
class UnitFacts:
    """The facts of one earlier unit, labelled for display (``S01E03``)."""

    label: str
    facts: Facts


@dataclass(frozen=True)
class Relationship:
    a: str
    b: str
    relation: str
    intimacy: int
    hierarchy: str
    since: str  # label of the unit in which this state was last shown
    confidence: float = 0.5
    detail: str = ""


@dataclass
class StoryState:
    characters: list[Character] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    terms: dict[str, int] = field(default_factory=dict)  # term -> number of units it appears in
    summaries: list[tuple[str, str]] = field(default_factory=list)  # (label, summary), story order


@dataclass
class StoryContext:
    """Prompt sections for one unit plus the fingerprint that decides when its translation is stale."""

    sections: list[tuple[str, str]]
    fingerprint: str
    compressed: str = ""  # "" | "model" | "truncated": how the older summaries were shortened


def _tokens(text: str) -> int:
    return -(-len(text) // _CHARS_PER_TOKEN)


# -- fingerprint ---------------------------------------------------------------------------------


def fingerprint(prior: Sequence[UnitFacts], own: Facts | None) -> str:
    """What a translation of this unit depends on: the facts of every unit before it, and its own.

    Stored with the translation; a different value now means a different context bundle (FR-5.5).
    """
    payload = json.dumps(
        [[u.label, u.facts.hash] for u in prior] + [own.hash if own is not None else ""], ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def entries_hash(entries: Sequence[tuple[str, str]]) -> str:
    """Cache key for a compression: the exact summaries being condensed."""
    return hashlib.sha256(json.dumps(list(entries), ensure_ascii=False).encode("utf-8")).hexdigest()


# -- fold ------------------------------------------------------------------------------------------


class _Names:
    """Resolve names, aliases and nicknames to one canonical spelling per character."""

    def __init__(self) -> None:
        self.canonical: dict[str, str] = {}  # casefolded name or alias -> canonical name
        self.characters: dict[str, Character] = {}  # keyed by casefolded canonical name

    def find(self, name: str) -> str | None:
        """The known character ``name`` refers to: an exact name or alias, or else the one character
        whose full name it abbreviates ("Ted" for "Ted Mosby": the given name, or the surname of a
        two-word name). Ambiguous abbreviations match nothing, so two people are never merged by guess."""
        key = name.casefold()
        exact = self.canonical.get(key)
        if exact is not None:
            return exact
        def abbreviates(short: str, full: str) -> bool:
            words = full.split()
            return len(short) >= 3 and " " not in short and len(words) > 1 and (
                words[0] == short or (len(words) == 2 and words[-1] == short)
            )

        hits = {canon for known, canon in self.canonical.items() if abbreviates(known, key) or abbreviates(key, known)}
        return hits.pop() if len(hits) == 1 else None

    def add(self, character: Character, taken: set[str] | None = None) -> None:
        """Register a character. ``taken`` holds the characters already listed in the same unit: two
        characters of one episode are two people, so a shared nickname never merges them."""
        names = [character.name, *character.aliases]
        known = next((self.find(n) for n in names if self.find(n) and self.find(n) not in (taken or ())), None)
        if known is None and character.name.casefold() in self.characters:
            return  # the name is already someone else in this unit
        if taken is not None:
            taken.add(known or character.name)
        if known is None:
            self.characters[character.name.casefold()] = character
            for n in names:
                self.canonical.setdefault(n.casefold(), character.name)
            return
        current = self.characters[known.casefold()]
        aliases = list(current.aliases)
        for n in names:
            if n.casefold() != known.casefold() and n not in aliases:
                aliases.append(n)
            self.canonical.setdefault(n.casefold(), known)
        self.characters[known.casefold()] = Character(
            known, tuple(aliases), current.gender or character.gender,
            character.age_hint or current.age_hint, character.role or current.role,
        )

    def resolve(self, name: str) -> str:
        found = self.find(name)
        if found is None:
            self.canonical[name.casefold()] = name
            self.characters.setdefault(name.casefold(), Character(name))
            return name
        return found


def fold(units: Sequence[UnitFacts]) -> StoryState:
    """Fold the facts of ``units`` (already in story order) into one state.

    Characters merge by name or alias; each unordered pair keeps its latest relationship event
    (latest unit, then latest cue within the unit); terms are counted per unit.
    """
    names = _Names()
    latest: dict[tuple[str, str], Relationship] = {}
    terms: dict[str, int] = {}
    summaries: list[tuple[str, str]] = []
    for unit in units:
        facts = unit.facts
        taken: set[str] = set()
        for character in facts.characters:
            names.add(character, taken)
        for event in facts.relationship_events:  # sorted by cue
            if event.confidence < MIN_CONFIDENCE:
                continue
            a, b = names.resolve(event.a), names.resolve(event.b)
            if a.casefold() == b.casefold():
                continue
            key = tuple(sorted((a.casefold(), b.casefold())))
            before = latest.get(key)
            if before and event.confidence < _OVERTURN_BELOW and before.confidence >= _CONFIDENT:
                continue  # a doubtful reading does not overturn what was clear
            latest.pop(key, None)  # re-insert so the dict stays ordered by recency
            latest[key] = Relationship(
                a, b, event.relation, event.intimacy, event.hierarchy, unit.label, event.confidence, event.detail
            )
        for term in facts.terms:
            terms[term.source] = terms.get(term.source, 0) + 1
        if facts.summary:
            summaries.append((unit.label, facts.summary))
    return StoryState(list(names.characters.values()), list(latest.values()), terms, summaries)


# -- rendering ---------------------------------------------------------------------------------------

_GENDER = {"f": "female", "m": "male", "other": "other"}
_HEDGE = (
    "Inferred automatically from the dialogue of earlier episodes, so it can be wrong. Use it to choose "
    "tone and forms of address; the glossary and the lines of this episode override it."
)


def _character_line(c: Character) -> str:
    bits = [b for b in (_GENDER.get(c.gender, ""), c.age_hint, c.role) if b]
    extra = f" ({', '.join(bits)})" if bits else ""
    aka = f", also called {', '.join(c.aliases)}" if c.aliases else ""
    return f"- {c.name}{extra}{aka}"


def _relationship_line(r: Relationship, *, show_since: bool = True) -> str:
    standing = {"a_above": f"{r.a} is the senior of the two", "b_above": f"{r.b} is the senior of the two"}.get(
        r.hierarchy, "equals"
    )
    since = f" (as of {r.since})" if show_since else ""
    detail = f" ({r.detail})" if r.detail else ""
    return f"- {r.a} & {r.b}: {r.relation}{detail}, closeness {r.intimacy}/{INTIMACY_MAX}, {standing}{since}"


def _first_sentence(text: str, limit: int = _OLD_SUMMARY_CHARS) -> str:
    cut = text.split(". ")[0].strip()
    return cut if len(cut) <= limit else cut[: limit - 1].rstrip() + "…"


def _join(entries: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {text}" for label, text in entries)


def _render(
    state: StoryState, budget_tokens: int, recent: int, compress: Compressor | None
) -> tuple[list[tuple[str, str]], str]:
    summaries = state.summaries
    keep = max(0, recent)
    older, newest = (summaries[:-keep], summaries[-keep:]) if keep else (summaries, [])

    characters = state.characters[-_MAX_FOLDED_CHARACTERS:]
    relationships = state.relationships[-_MAX_FOLDED_RELATIONSHIPS:]

    def story_text(arc: str) -> str:
        parts = []
        if arc:
            parts.append(arc)
        parts.extend(f"{label}: {text}" for label, text in newest)
        return "\n".join(parts)

    def people_text() -> str:
        lines = [_HEDGE]
        if characters:
            lines.append("Characters so far:")
            lines.extend(_character_line(c) for c in characters)
        if relationships:
            lines.append("Where each pair stands now:")
            lines.extend(_relationship_line(r) for r in relationships)
        return "\n".join(lines)

    def total(arc: str) -> int:
        return _tokens(story_text(arc)) + (_tokens(people_text()) if (characters or relationships) else 0)

    how = ""
    arc = ("Earlier episodes: " + "\n".join(f"{label}: {text}" for label, text in older)) if older else ""
    if older and total(arc) > budget_tokens:
        condensed = ""
        if compress is not None:
            try:
                condensed = compress(list(older)).strip()
            except BadOutput:  # an unusable answer falls back to truncation; quota and outages still propagate
                condensed = ""
        if condensed:
            arc, how = f"Earlier episodes ({older[0][0]} to {older[-1][0]}), condensed: {condensed}", "model"
        else:
            arc = "Earlier episodes (first sentence of each): " + " | ".join(
                f"{label}: {_first_sentence(text)}" for label, text in older
            )
            how = "truncated"
        if total(arc) > budget_tokens:
            # Still too long: drop the oldest relationships first (the newest state matters most).
            while relationships and total(arc) > budget_tokens:
                relationships = relationships[1:]
            while characters and total(arc) > budget_tokens:
                characters = characters[1:]
            if total(arc) > budget_tokens and len(arc) > 2000:
                arc = arc[:2000].rstrip() + "…"

    sections: list[tuple[str, str]] = []
    story = story_text(arc)
    if story:
        sections.append(("Story so far", story))
    if characters or relationships:
        sections.append(("Relationship state", people_text()))
    return sections, how


def render_episode(facts: Facts | None) -> tuple[str, str] | None:
    """The section for the unit being translated: its own summary and relationship changes by item."""
    if facts is None or facts.is_empty():
        return None
    lines = ["Taken from this episode's own subtitles; item numbers are the `i` values you are given."]
    if facts.summary:
        lines.append(f"Summary: {facts.summary}")
    shown = [e for e in facts.relationship_events if e.confidence >= MIN_CONFIDENCE]
    if shown:
        lines.append("Changes in how characters stand to each other (apply from that item on):")
        for e in shown:
            standing = {"a_above": f"{e.a} senior", "b_above": f"{e.b} senior"}.get(e.hierarchy, "equals")
            quote = f' — "{e.evidence}"' if e.evidence else ""
            detail = f" ({e.detail})" if e.detail else ""
            lines.append(
                f"- from item {e.cue}: {e.a} & {e.b}: {e.relation}{detail}, "
                f"closeness {e.intimacy}/{INTIMACY_MAX}, {standing}{quote}"
            )
    return "This episode", "\n".join(lines)


def build_context(
    prior: Sequence[UnitFacts],
    own: Facts | None,
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    recent: int = DEFAULT_RECENT_UNITS,
    compress: Compressor | None = None,
) -> StoryContext:
    """Context sections for a unit from the facts of the units before it and its own facts.

    ``prior`` must hold only units earlier in story order; the caller owns that selection.
    """
    sections, how = _render(fold(prior), max(500, budget_tokens), recent, compress)
    episode = render_episode(own)
    if episode:
        sections.append(episode)
    return StoryContext(sections, fingerprint(prior, own), how)
