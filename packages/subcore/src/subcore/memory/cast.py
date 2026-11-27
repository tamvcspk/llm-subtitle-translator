"""The characters of one episode, as names (FR-5.11): the series' main cast plus the episode's own.

Pure and told nothing about where a name came from: the caller may hand over the names an outside list (TMDB) gives, the
engine only receives names. With no outside list, the main cast is who the facts of earlier episodes show in
``MIN_MAIN_UNITS`` or more of them. The result goes into the bundle (``cast``) and decides which forms of address the
prompt lists.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from .address import clean_facts, is_generic_address
from .facts import Facts
from .fold import UnitFacts, _Names

__all__ = ["MIN_MAIN_UNITS", "clean_cast_name", "episode_cast", "recurring_names"]

MIN_MAIN_UNITS = 3  # episodes of the facts a character must be in to count as main when no cast list is known
_PARENS = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_NOT_A_ROLE = {"self", "himself", "herself", "themselves", "narrator", "uncredited", "various", "unknown"}


def clean_cast_name(raw: str) -> str:
    """A cast list's character name as a plain name: ``"Lois (voice)"`` → ``"Lois"``; "Self" and the like, and a form
    of address ("Mom"), → ``""``."""
    name = " ".join(_PARENS.sub("", str(raw or "")).split())
    return "" if not name or name.casefold() in _NOT_A_ROLE or is_generic_address(name) else name


def recurring_names(prior: Sequence[UnitFacts], *, min_units: int = MIN_MAIN_UNITS, lang: str | None = None) -> list[str]:
    """Characters the facts of earlier episodes show in ``min_units`` or more of them, in order of first appearance."""
    names = _Names()
    seen: dict[str, set[str]] = {}
    order: list[str] = []
    for unit in prior:
        taken: set[str] = set()
        for character in clean_facts(unit.facts, lang).characters:
            names.add(character, taken)
            canonical = names.find(character.name) or character.name
            labels = seen.setdefault(canonical.casefold(), set())
            if not labels:
                order.append(canonical.casefold())
            labels.add(unit.label)
    return [names.characters[k].name for k in order if k in names.characters and len(seen[k]) >= min_units]


def episode_cast(
    prior: Sequence[UnitFacts],
    own: Facts | None,
    *,
    main: Iterable[str] = (),
    guests: Iterable[str] = (),
    min_units: int = MIN_MAIN_UNITS,
    lang: str | None = None,
) -> list[str]:
    """The names of the characters of the episode: ``main`` (an outside list of the series' main cast, else the
    recurring ones of ``prior``), the episode's own ``guests`` and the characters (with aliases) of ``own``.
    Empty when nothing is known, which tells the prompt to keep every rule."""
    base = [n for n in (clean_cast_name(m) for m in main) if n] or recurring_names(prior, min_units=min_units, lang=lang)
    names = list(base)
    names.extend(n for n in (clean_cast_name(g) for g in guests) if n)
    if own is not None:
        for character in clean_facts(own, lang).characters:
            names.extend((character.name, *character.aliases))
    return list(dict.fromkeys(n for n in names if n))
