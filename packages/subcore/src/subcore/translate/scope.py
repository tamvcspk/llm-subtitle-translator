"""Who is in the prompt of one episode: scope of forms of address and the glossary-wins rule (FR-5.11, FR-5.12).

Pure: it reads the names the bundle holds (the glossary, the active address rules and ``cast``) and decides which
lines of the system prompt are left out. Nothing here reads a word of the dialogue: two people can talk a whole scene
in pronouns, so a rule is kept or dropped by *who* is in the episode, never by what the lines say.

``cast`` is the episode's list of character names, put in the bundle by whoever gathers it (the worker: the main cast
of the series plus the episode's own characters). An empty ``cast`` means nothing is known: every rule stays.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Sequence

from ..glossary import ActiveRule, Glossary
from ..style import CharacterCard

__all__ = ["Names", "ruled_pairs", "scoped_rules", "without_ruled_addresses"]

_MIN_WORD = 3


def _words(name: str) -> frozenset[str]:
    return frozenset(w for w in "".join(ch if ch.isalnum() or ch == "'" else " " for ch in name.casefold()).split() if w)


class Names:
    """Do two names stand for one person? Equal ignoring case, one the whole words of the other ("Lois" and
    "Lois Wilkerson"), or one an alias the glossary gives the other. Two different short names never match."""

    def __init__(self, glossary: Glossary | None = None) -> None:
        self._forms: dict[str, frozenset[str]] = {}
        for character in glossary.characters if glossary else ():
            forms = frozenset((character.name.casefold(), *(a.casefold() for a in character.aliases)))
            for form in forms:
                self._forms[form] = self._forms.get(form, frozenset()) | forms

    def forms(self, name: str) -> frozenset[str]:
        key = " ".join(name.split()).casefold()
        return self._forms.get(key, frozenset()) | {key}

    def same(self, a: str, b: str) -> bool:
        for x in self.forms(a):
            for y in self.forms(b):
                if x == y:
                    return True
                wx, wy = _words(x), _words(y)
                small, big = (wx, wy) if len(wx) <= len(wy) else (wy, wx)
                if small and small <= big and all(len(w) >= _MIN_WORD for w in small):
                    return True
        return False

    def among(self, name: str, others: Iterable[str]) -> bool:
        return any(self.same(name, other) for other in others)


def scoped_rules(rules: Sequence[ActiveRule], cast: Sequence[str], glossary: Glossary | None) -> list[ActiveRule]:
    """The rules whose two people are both in ``cast`` (FR-5.11). With no cast, all of them."""
    if not cast:
        return list(rules)
    names = Names(glossary)
    return [r for r in rules if names.among(r.rule.a, cast) and names.among(r.rule.b, cast)]


def ruled_pairs(rules: Sequence[ActiveRule], glossary: Glossary | None):
    """``ruled(a, b)``: does an active rule of this episode already settle how ``a`` and ``b`` address each other?"""
    names = Names(glossary)
    pairs = [(r.rule.a, r.rule.b) for r in rules]

    def ruled(a: str, b: str) -> bool:
        return any(
            (names.same(a, x) and names.same(b, y)) or (names.same(a, y) and names.same(b, x)) for x, y in pairs
        )

    return ruled


def without_ruled_addresses(cards: Sequence[CharacterCard], rules: Sequence[ActiveRule], glossary: Glossary | None):
    """The character cards without the ``Addresses X`` lines of a pair a rule settles: the glossary wins (FR-5.12)."""
    if not rules:
        return list(cards)
    ruled = ruled_pairs(rules, glossary)
    out: list[CharacterCard] = []
    for card in cards:
        kept = tuple((to, form) for to, form in card.address if not any(ruled(n, to) for n in card.names))
        out.append(card if kept == card.address else replace(card, address=kept))
    return out
