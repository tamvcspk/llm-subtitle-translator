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


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


class Names:
    """Do two names stand for one person? Equal ignoring case, one an alias the glossary gives the other, or one the
    whole words of the other ("Lois" and "Lois Wilkerson") when that is not a guess between several people.

    Two separate glossary entries are two people unless one declares the other as an alias ("George" and "George
    Michael"), and a short name matches by words only when exactly one person among the glossary and ``cast`` carries
    it ("Wilkerson" shared by two people matches neither), so a rule is never read for the wrong person (R19-6)."""

    def __init__(self, glossary: Glossary | None = None, cast: Iterable[str] = ()) -> None:
        self._forms: dict[str, frozenset[str]] = {}
        self._entry: dict[str, int] = {}  # form -> index of the glossary character that owns it
        known: set[str] = set()
        for index, character in enumerate(glossary.characters if glossary else ()):
            forms = frozenset((_key(character.name), *(_key(a) for a in character.aliases)))
            for form in forms:
                self._forms[form] = self._forms.get(form, frozenset()) | forms
                self._entry.setdefault(form, index)
            known |= forms
        known |= {_key(n) for n in cast if _key(n)}
        self._known = {form: _words(form) for form in known}

    def forms(self, name: str) -> frozenset[str]:
        key = _key(name)
        return self._forms.get(key, frozenset()) | {key}

    def _person(self, form: str) -> object:
        entry = self._entry.get(form)
        return form if entry is None else entry

    def _only_one(self, small: frozenset[str], big_form: str) -> bool:
        """Does ``small`` (the words of a short name) belong to one person only, ``big_form``'s?"""
        owners = {self._person(form) for form, words in self._known.items() if small < words}
        return owners <= {self._person(big_form)}

    def same(self, a: str, b: str) -> bool:
        pairs = [(x, y) for x in self.forms(a) for y in self.forms(b)]
        if any(x == y for x, y in pairs):
            return True
        for x, y in pairs:
            ex, ey = self._entry.get(x), self._entry.get(y)
            if ex is not None and ey is not None and ex != ey:
                continue  # two entries of the glossary are two people
            wx, wy = _words(x), _words(y)
            small, big, big_form = (wx, wy, y) if len(wx) <= len(wy) else (wy, wx, x)
            if small and small <= big and all(len(w) >= _MIN_WORD for w in small) and self._only_one(small, big_form):
                return True
        return False

    def among(self, name: str, others: Iterable[str]) -> bool:
        return any(self.same(name, other) for other in others)


def scoped_rules(rules: Sequence[ActiveRule], cast: Sequence[str], glossary: Glossary | None) -> list[ActiveRule]:
    """The rules whose two people are both in ``cast`` (FR-5.11). With no cast, all of them."""
    if not cast:
        return list(rules)
    names = Names(glossary, cast)
    return [r for r in rules if names.among(r.rule.a, cast) and names.among(r.rule.b, cast)]


def ruled_pairs(rules: Sequence[ActiveRule], glossary: Glossary | None, cast: Iterable[str] = ()):
    """``ruled(a, b)``: does an active rule of this episode already settle how ``a`` and ``b`` address each other?

    Only a rule in force from the start of the episode (``from_cue is None``) settles the pair: one that begins mid-way
    leaves the pair's state before it to the inferred line (R19-5). ``rules`` should be the ones in scope."""
    names = Names(glossary, cast)
    pairs = [(r.rule.a, r.rule.b) for r in rules if r.from_cue is None]

    def ruled(a: str, b: str) -> bool:
        return any(
            (names.same(a, x) and names.same(b, y)) or (names.same(a, y) and names.same(b, x)) for x, y in pairs
        )

    return ruled


def without_ruled_addresses(
    cards: Sequence[CharacterCard], rules: Sequence[ActiveRule], glossary: Glossary | None, cast: Iterable[str] = (),
):
    """The character cards without the ``Addresses X`` lines of a pair a rule settles: the glossary wins (FR-5.12)."""
    if not rules:
        return list(cards)
    ruled = ruled_pairs(rules, glossary, cast)
    out: list[CharacterCard] = []
    for card in cards:
        kept = tuple((to, form) for to, form in card.address if not any(ruled(n, to) for n in card.names))
        out.append(card if kept == card.address else replace(card, address=kept))
    return out
