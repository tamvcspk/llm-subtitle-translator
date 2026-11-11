"""Detect model replies whose text is attached to the wrong cue number (drift).

A weak model can answer ``{"i": 241, "text": <translation of cue 238>}`` and keep that
shift for hundreds of cues. Every id is present and well-formed, so the per-cue
checks in :mod:`validate` accept it, yet each line shows at the wrong timecode.

Two language-independent signals, both heuristics that only trigger repair and
warnings, never a failure:

* **Anchors** survive translation: digit groups and capitalised names in the middle
  of a sentence. A cue is *displaced* when none of its own anchors appear in its
  translation, but one does appear in the translation of a nearby cue whose
  *source* does not contain it. Verified cues (all anchors present) bound the
  drifting region, so cues without anchors inside it are caught too.
* **Length profile**: the length of a translation tracks the length of its source
  (Pearson correlation ~0.8 when aligned). In a sliding window, if the same
  profile correlates clearly better after shifting by ``k`` cues, the window is
  drifting. This catches stretches that contain no digits or names.
"""

from __future__ import annotations

import re
from statistics import fmean, pstdev

__all__ = ["anchors_of", "find_misaligned", "NEIGHBOURS", "MIN_DISPLACED"]

NEIGHBOURS = 6  # how far (in cues) a shifted translation is searched for
MIN_DISPLACED = 2  # a lone mismatch is not evidence of drift

WINDOW = 20  # cues per length-profile window
STEP = 10
MAX_SHIFT = 8
MIN_SPREAD = 6.0  # a window of near-equal lengths says nothing
MIN_SHIFTED_CORR = 0.6
MIN_GAIN = 0.3  # shifted correlation must beat the unshifted one by this much

_TAG = re.compile(r"<[^>]+>")
_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
# Capitalised word after a lowercase letter or comma: a name, not a sentence start.
_SOURCE_NAME = re.compile(r"(?<=[a-z,] )[A-Z][A-Za-z'’-]{2,}")
_ANY_NAME = re.compile(r"[A-Z][A-Za-z'’-]{2,}")
_POSSESSIVE = re.compile(r"['’]s$")


def _name(token: str) -> str:
    return "n:" + _POSSESSIVE.sub("", token)


def _numbers(text: str) -> set[str]:
    return {"d:" + re.sub(r"\D", "", m) for m in _NUMBER.findall(text)}


def anchors_of(source: str) -> set[str]:
    """Anchors expected to appear (unchanged) in any translation of ``source``."""
    text = _TAG.sub("", source)
    return _numbers(text) | {_name(m) for m in _SOURCE_NAME.findall(text)}


def _present(translated: str) -> set[str]:
    """Everything in ``translated`` that could match an anchor."""
    text = _TAG.sub("", translated)
    return _numbers(text) | {_name(m) for m in _ANY_NAME.findall(text)}


def _length(text: str) -> int:
    return len(_TAG.sub("", text).replace("\n", " ").strip())


def _corr(a: list[int], b: list[int]) -> float | None:
    if len(a) < 8:
        return None
    sa, sb = pstdev(a), pstdev(b)
    if sa < MIN_SPREAD or sb < MIN_SPREAD:
        return None
    ma, mb = fmean(a), fmean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a) / (sa * sb)


def _length_drift(judged: list[int], source: dict[int, str], out: dict[int, str]) -> set[int]:
    src = [_length(source[n]) for n in judged]
    dst = [_length(out[n]) for n in judged]
    count = len(judged)

    def corr(rng: range, k: int) -> float | None:
        pairs = [(src[j], dst[j + k]) for j in rng if 0 <= j + k < count]
        return _corr([p[0] for p in pairs], [p[1] for p in pairs])

    starts = list(range(0, max(1, count - WINDOW + 1), STEP))
    if count > WINDOW and starts[-1] != count - WINDOW:
        starts.append(count - WINDOW)  # always cover the tail
    flagged: set[int] = set()
    for s in starts:
        rng = range(s, min(s + WINDOW, count))
        base = corr(rng, 0)
        best, best_k = None, 0
        for k in range(-MAX_SHIFT, MAX_SHIFT + 1):
            if k == 0:
                continue
            c = corr(rng, k)
            if c is not None and (best is None or c > best):
                best, best_k = c, k
        if best is not None and best >= MIN_SHIFTED_CORR and best - (base if base is not None else 0.0) >= MIN_GAIN:
            flagged.update(judged[j] for j in rng)
    return flagged


_SHIFT_NOTE = (
    "your answer looks shifted: this item's text seems to belong to a different "
    "item number. Translate each item only from its own source text"
)


def find_misaligned(ids: list[int], source: dict[int, str], out: dict[int, str]) -> dict[int, str]:
    """Return ``{cue id: reason}`` for the cues to re-request, or ``{}`` when aligned.

    ``ids`` are the part's cue ids in order; only ids present in ``out`` are judged.
    """
    judged = [n for n in sorted(ids) if n in out and n in source]
    flagged = {n: _SHIFT_NOTE for n in _length_drift(judged, source, out)}
    flagged.update(_anchor_drift(judged, source, out))
    return flagged


def _anchor_drift(judged: list[int], source: dict[int, str], out: dict[int, str]) -> dict[int, str]:
    expected = {n: anchors_of(source[n]) for n in judged}
    present = {n: _present(out[n]) for n in judged}

    verified: set[int] = set()
    displaced: set[int] = set()
    for pos, n in enumerate(judged):
        want = expected[n]
        if not want:
            continue
        own = want & present[n]
        if len(own) == len(want):
            verified.add(n)
        elif not own:
            lo, hi = max(0, pos - NEIGHBOURS), pos + NEIGHBOURS + 1
            for m in judged[lo:hi]:
                if m != n and (want & present[m]) - expected[m]:
                    displaced.add(n)
                    break

    if len(displaced) < MIN_DISPLACED:
        return {}

    # One cluster per run of displaced cues not interrupted by a verified cue.
    flagged: dict[int, str] = {}
    cluster: list[int] = []

    def close() -> None:
        if len(cluster) < MIN_DISPLACED:
            return
        first, last = judged.index(cluster[0]), judged.index(cluster[-1])
        start = first
        while start > 0 and judged[start - 1] not in verified:
            start -= 1
        end = last
        while end < len(judged) - 1 and judged[end + 1] not in verified:
            end += 1
        for n in judged[start : end + 1]:
            flagged[n] = _SHIFT_NOTE

    previous = None
    for n in sorted(displaced):
        if previous is not None and any(previous < v < n for v in verified):
            close()
            cluster = []
        cluster.append(n)
        previous = n
    close()
    return flagged
