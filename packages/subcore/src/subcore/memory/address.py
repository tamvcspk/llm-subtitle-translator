"""Generic forms of address are not names (FR-5.9, D-02, SPEC #30).

"Mom", "Dad", "Sir" and the like say how one person addresses another, so they are relative: Lois calls Ida
"Mom" and Malcolm calls Lois "Mom". Folded as a character name or alias they merge different people into one
(every "X & Mom" collapsing onto Lois). This module is pure and works on stored facts at read time: the stored
facts are never edited, so ``Facts.hash`` (and with it every translation fingerprint) stays what it was.

The table is per source language and open for more languages; an unknown language falls back to English.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable

from .facts import Character, Facts, RelationshipEvent

__all__ = ["GENERIC_ADDRESS", "clean_facts", "generic_terms", "is_generic_address", "merge_declared"]

GENERIC_ADDRESS: dict[str, frozenset[str]] = {
    "en": frozenset({
        "mom", "mommy", "mama", "mum", "mummy", "ma", "mother", "dad", "daddy", "papa", "pa", "pop", "pops", "father",
        "grandma", "grandpa", "granny", "grandmother", "grandfather", "nana", "grandpop",
        "sir", "ma'am", "madam", "miss",
        "honey", "sweetie", "sweetheart", "darling", "dear", "baby",
        "son", "kid", "boss", "doc", "coach", "buddy", "pal", "bro", "sis", "brother", "sister",
        "uncle", "aunt", "auntie",
    }),
}

_EDGE = re.compile(r"^[\W_]+|[\W_]+$")
_ARTICLE = re.compile(r"^(?:the|my|your|our)\s+", re.IGNORECASE)


def generic_terms(lang: str | None = None) -> frozenset[str]:
    """The generic forms of address of a source language (``en``, ``en-US``, ``English``...); English when unknown."""
    key = (lang or "en").strip().casefold().replace("_", "-").split("-")[0]
    return GENERIC_ADDRESS.get(key) or GENERIC_ADDRESS["en"]


def is_generic_address(name: str, lang: str | None = None) -> bool:
    """Is ``name`` only a form of address ("Mom", "Sir", "my son")? A proper name that merely contains one is not."""
    text = _ARTICLE.sub("", " ".join(str(name).split()).casefold())
    text = _EDGE.sub("", text.replace("’", "'"))
    return text in generic_terms(lang)


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def merge_declared(characters: Iterable[Character]) -> list[Character]:
    """Merge entries of ONE unit when one declares the other as an alias (``Mom (aka Lois)`` + ``Lois``).

    A declared alias wins over the rule that two entries of an episode are two people; two entries that only share a
    nickname, with neither naming the other, stay two. The merged name is the longer proper name (first on a tie).
    """
    merged: list[Character] = []
    for character in characters:
        names = [character.name, *character.aliases]
        hit = next(
            (
                i for i, other in enumerate(merged)
                if _same(character.name, other.name)
                or any(_same(n, other.name) for n in character.aliases)
                or any(_same(n, character.name) for n in other.aliases)
            ),
            None,
        )
        if hit is None:
            merged.append(character)
            continue
        other = merged[hit]
        first, second = (character, other) if len(character.name) > len(other.name) else (other, character)
        aliases: list[str] = []
        for n in (*first.aliases, second.name, *second.aliases, *(x for x in names if x)):
            if not _same(n, first.name) and not any(_same(n, a) for a in aliases):
                aliases.append(n)
        merged[hit] = Character(
            first.name, tuple(aliases), first.gender or second.gender, first.age_hint or second.age_hint,
            first.role or second.role,
        )
    return merged


def clean_facts(facts: Facts, lang: str | None = None) -> Facts:
    """The facts of one unit without generic forms of address as names (FR-5.9.1).

    A character whose name is generic takes its first proper alias as the name (none: dropped); generic aliases
    vanish. An event with a generic end is renamed to the one proper name that unit's characters tie to that form
    ("Mom" declared as Lois's alias), and dropped when there is none or more than one (Lois's "Mom" and Ida's).
    """
    ties: dict[str, set[str]] = {}
    characters: list[Character] = []
    for c in facts.characters:
        proper = [n for n in (c.name, *c.aliases) if not is_generic_address(n, lang)]
        if not proper:
            continue
        for n in (c.name, *c.aliases):
            if is_generic_address(n, lang):
                ties.setdefault(" ".join(n.split()).casefold(), set()).add(proper[0].casefold())
        name = proper[0]
        characters.append(Character(name, tuple(proper[1:]), c.gender, c.age_hint, c.role))
    characters = merge_declared(characters)
    proper_name = {c.name.casefold(): c.name for c in characters}
    for c in characters:
        for n in c.aliases:
            proper_name.setdefault(n.casefold(), c.name)

    def resolve(name: str) -> str | None:
        if not is_generic_address(name, lang):
            return name
        found = ties.get(" ".join(name.split()).casefold(), set())
        if len(found) != 1:
            return None
        key = next(iter(found))
        return proper_name.get(key, key)

    events: list[RelationshipEvent] = []
    for e in facts.relationship_events:
        a, b = resolve(e.a), resolve(e.b)
        if a is None or b is None or _same(a, b):
            continue
        events.append(e if (a, b) == (e.a, e.b) else replace(e, a=a, b=b))
    if len(characters) == len(facts.characters) and events == list(facts.relationship_events) and all(
        x == y for x, y in zip(characters, facts.characters)
    ):
        return facts
    return replace(facts, characters=tuple(characters), relationship_events=tuple(events))
