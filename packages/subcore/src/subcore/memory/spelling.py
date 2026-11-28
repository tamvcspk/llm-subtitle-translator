"""One spelling per character: a name the facts spell a little differently from the cast list (FR-5.10a, D-08).

The model that reads a subtitle hears "Carolyn" where the cast list (TMDB) has "Caroline Miller". Left alone, the story
state, the glossary and the cast would carry three spellings of one person. This module is pure and works on names it is
handed (the caller owns where the cast list came from, ADR-2); stored facts are never edited, so ``Facts.hash`` and every
fingerprint stay what they were.

The merge is deliberately narrow, because a wrong merge is worse than a missing one:

- the name has no exact twin in the cast list and is not a name or alias of a glossary character (``keep``);
- exactly **one** cast name is close to it: same first letter, first words within two edits, the other words equal;
- two cast names that are both close mean nothing is merged, and two glossary characters are never merged with each other.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping

from .facts import Character, Facts, RelationshipEvent

__all__ = ["MAX_EDITS", "MIN_WORD", "rename_facts", "spelling_map"]

MAX_EDITS = 2  # edits between the first words of a facts name and a cast name
MIN_WORD = 4  # shorter first words are too easily another name ("Dana", "Dina" are, "Al", "Ed" are not names to guess)


def _words(name: str) -> list[str]:
    return " ".join(str(name).split()).casefold().split()


def _edits(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        row = [i]
        for j, cb in enumerate(b, start=1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = row
    return previous[-1]


def _close(name: list[str], full: list[str]) -> bool:
    """Is ``name`` a misspelling of ``full``: the first word a few edits away, any further words the same?"""
    if not name or len(name) > len(full) or name[1:] != full[1:len(name)]:
        return False
    first, other = name[0], full[0]
    return (
        first != other and len(first) >= MIN_WORD and len(other) >= MIN_WORD and first[0] == other[0]
        and _edits(first, other) <= MAX_EDITS
    )


def spelling_map(names: Iterable[str], cast: Iterable[str], keep: Iterable[str] = ()) -> dict[str, str]:
    """``{name in the facts: the cast list's spelling}`` for the names that are a misspelling of exactly one cast name.

    ``keep`` holds the names and aliases the glossary already gives to characters: those are the admin's spelling and are
    never renamed. Empty when there is no cast list."""
    cast_names = list(dict.fromkeys(n for n in cast if _words(n)))
    if not cast_names:
        return {}
    known = {" ".join(_words(n)) for n in (*cast_names, *keep)}
    full_words = [_words(c) for c in cast_names]
    out: dict[str, str] = {}
    for name in dict.fromkeys(names):
        words = _words(name)
        if not words or " ".join(words) in known:
            continue
        if any(words == full[:len(words)] for full in full_words):
            continue  # "Lois" for "Lois Wilkerson" is a shorter name, not another spelling
        hits = {" ".join(full): cast for full, cast in zip(full_words, cast_names) if _close(words, full)}
        if len(hits) == 1:
            out[name] = next(iter(hits.values()))
    return out


def rename_facts(facts: Facts, mapping: Mapping[str, str]) -> Facts:
    """The facts with every name in ``mapping`` spelled as the cast list does (names, aliases and the ends of events)."""
    if not mapping:
        return facts

    def fix(name: str) -> str:
        return mapping.get(name, name)

    characters = []
    for c in facts.characters:
        name = fix(c.name)
        aliases = tuple(dict.fromkeys(a for a in map(fix, c.aliases) if a.casefold() != name.casefold()))
        characters.append(c if (name, aliases) == (c.name, c.aliases) else Character(name, aliases, c.gender, c.age_hint, c.role))
    events: list[RelationshipEvent] = []
    for e in facts.relationship_events:
        a, b = fix(e.a), fix(e.b)
        if a.casefold() == b.casefold():
            continue
        events.append(e if (a, b) == (e.a, e.b) else replace(e, a=a, b=b))
    return replace(facts, characters=tuple(characters), relationship_events=tuple(events))
