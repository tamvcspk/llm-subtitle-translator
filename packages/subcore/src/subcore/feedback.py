"""What an admin keeps correcting, found in the cues they edited (FR-19.6a). Pure: no database, no model.

An :class:`EditedCue` is one cue as the machine wrote it (``before``), as the admin left it (``after``) and the source
line it came from. :func:`find_patterns` looks for the same small replacement (one to three words swapped for one to
three others) in several different cues, and for the word of the *source* that all of those cues share, which is what a
glossary term needs: ``Mom`` is always ``Má`` here, not ``Mẹ``.

Nothing here decides anything: a pattern is only a candidate the caller turns into a pending suggestion.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Hashable, Iterable, Sequence

from . import matching

__all__ = ["EditedCue", "Pattern", "find_patterns", "replacements", "MIN_REPEATS"]

MIN_REPEATS = 3  # criterion 20a: the same replacement in this many different cues
MAX_PHRASE_WORDS = 3  # a longer swap is a rewrite, not a word the admin keeps changing
MAX_SWAPS_PER_CUE = 4  # a cue with more swaps than this was rewritten: it shows no pattern
MAX_BACKGROUND_SHARE = 0.5  # a source word that half of the other edited cues have too says nothing (``the``)
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class EditedCue:
    """One corrected cue. ``key`` says which cue it is (the caller's id), so a cue is never counted twice."""

    key: Hashable
    source: str
    before: str
    after: str
    where: str = ""  # a label for the evidence: ``S01E02``
    cue: int = 0


@dataclass(frozen=True)
class Pattern:
    """The same swap in several cues. ``old`` and ``new`` are written the way most of those cues wrote them;
    ``source`` is the word of the source line all those cues share (empty when there is none)."""

    old: str
    new: str
    cues: tuple[EditedCue, ...]
    source: str = ""

    @property
    def count(self) -> int:
        return len(self.cues)


def _words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group().casefold(), m.start(), m.end()) for m in _WORD.finditer(text)]


def replacements(before: str, after: str) -> list[tuple[str, str]]:
    """The word swaps that turn ``before`` into ``after``, as written (case and spacing of the text). Only a swap of
    one to three words for one to three words counts: a word the admin added or took out says nothing about what
    to put in its place, and a change of capital letters alone is not a swap."""
    a, b = _words(before), _words(after)
    matcher = SequenceMatcher(None, [w for w, _, _ in a], [w for w, _, _ in b], autojunk=False)
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or not (1 <= i2 - i1 <= MAX_PHRASE_WORDS and 1 <= j2 - j1 <= MAX_PHRASE_WORDS):
            continue
        out.append((before[a[i1][1]:a[i2 - 1][2]], after[b[j1][1]:b[j2 - 1][2]]))
    return out


def _fold(value: str) -> str:
    return " ".join(value.split()).casefold()


def _source_word(members: Sequence[EditedCue], others: Sequence[EditedCue]) -> str:
    """The word every cue of the pattern has in its source line and few other edited cues have, or ''.

    Words of text written without spaces (Chinese, Japanese, Thai...) are not found: they have no words to intersect.
    """
    shared: set[str] | None = None
    for cue in members:
        found = {w for w, _, _ in _words(cue.source) if len(w) >= 2 and not w.isdigit()}
        shared = found if shared is None else shared & found
        if not shared:
            return ""
    candidates = [w for w in (shared or ()) if all(matching.contains(c.source, w) for c in members)]
    if not candidates:
        return ""

    def background(word: str) -> float:
        return sum(1 for c in others if matching.contains(c.source, word)) / len(others) if others else 0.0

    ranked = sorted((background(w), -len(w), w) for w in candidates)
    share, _, word = ranked[0]
    return word if share <= MAX_BACKGROUND_SHARE else ""


def find_patterns(edits: Iterable[EditedCue], *, min_repeats: int = MIN_REPEATS) -> list[Pattern]:
    """Swaps made in at least ``min_repeats`` different cues, the most repeated first. A cue is one occurrence of a
    swap however many times the swap shows in it."""
    cues = list(edits)
    groups: dict[tuple[str, str], tuple[Counter[tuple[str, str]], dict[Hashable, EditedCue]]] = {}
    for cue in cues:
        swaps = replacements(cue.before, cue.after)
        if not swaps or len(swaps) > MAX_SWAPS_PER_CUE:
            continue
        for old, new in swaps:
            forms, members = groups.setdefault((_fold(old), _fold(new)), (Counter(), {}))
            forms[(old, new)] += 1
            members.setdefault(cue.key, cue)
    found: list[Pattern] = []
    for forms, members in groups.values():
        if len(members) < min_repeats:
            continue
        # The form most cues were written in; a tie goes to the one with fewer capital letters ("má" over "Má"), which
        # is the form that is right in the middle of a sentence too.
        old, new = min(forms, key=lambda f: (-forms[f], sum(ch.isupper() for ch in f[1]), f))
        inside = list(members.values())
        others = [c for c in cues if c.key not in members]
        found.append(Pattern(old, new, tuple(inside), _source_word(inside, others)))
    return sorted(found, key=lambda p: (-p.count, p.old.casefold(), p.new.casefold()))
