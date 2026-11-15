"""Choose few-shot examples from lines an admin already corrected (FR-18.5, ADR-14 point 5).

Pure and deterministic: the same candidates and the same text always give the same examples, which keeps a bundle's
hash stable. Closeness is lexical (words shared with the text being translated, rarer words counting more); there are
no embeddings. A budget in tokens bounds the section, so a style with many corrections cannot make every prompt long.
"""

from __future__ import annotations

import math
import re
from typing import Iterable

__all__ = ["estimate_tokens", "select"]

_WORD = re.compile(r"\w+", re.UNICODE)
_MIN_WORD = 4  # shorter words ("the", "and", "you") carry no similarity


def estimate_tokens(text: str) -> int:
    """A cheap upper-ish estimate (3 characters a token): enough to keep a section inside a budget."""
    return max(1, math.ceil(len(text) / 3))


def _words(text: str) -> set[str]:
    return {w for w in (m.group(0).casefold() for m in _WORD.finditer(text)) if len(w) >= _MIN_WORD}


def select(
    candidates: Iterable[tuple[str, str]], texts: Iterable[str], budget_tokens: int, *, limit: int = 12,
) -> tuple[tuple[str, str], ...]:
    """The ``(source, translation)`` pairs of ``candidates`` closest to ``texts``, best first, within ``budget_tokens``.

    A pair that shares no word with ``texts`` is never chosen: an unrelated example only costs tokens. A budget of 0
    (or a ``limit`` of 0) turns the feature off."""
    if budget_tokens <= 0 or limit <= 0:
        return ()
    query: set[str] = set()
    for text in texts:
        query |= _words(text)
    if not query:
        return ()
    pairs = sorted({(s.strip(), t.strip()) for s, t in candidates if s.strip() and t.strip()})
    if not pairs:
        return ()
    sets = [_words(s) for s, _ in pairs]
    document_frequency: dict[str, int] = {}
    for words in sets:
        for word in words:
            document_frequency[word] = document_frequency.get(word, 0) + 1
    total = len(pairs)

    def idf(word: str) -> float:
        return math.log(1 + total / document_frequency.get(word, 1))

    scored: list[tuple[float, int, tuple[str, str]]] = []
    for pair, words in zip(pairs, sets):
        shared = words & query
        if not shared:
            continue
        score = sum(idf(w) for w in shared) / math.sqrt(len(words))
        scored.append((-score, len(pair[0]) + len(pair[1]), pair))
    chosen: list[tuple[str, str]] = []
    spent = 0
    for _, _, pair in sorted(scored):
        cost = estimate_tokens(pair[0]) + estimate_tokens(pair[1]) + 4
        if spent + cost > budget_tokens:
            continue  # a shorter, less similar pair may still fit
        chosen.append(pair)
        spent += cost
        if len(chosen) >= limit:
            break
    return tuple(chosen)
