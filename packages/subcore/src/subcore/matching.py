"""Does a source line mention a glossary name? The one answer for the whole engine (R12-2, R12-10).

Four places ask this question: which terms a big glossary lists for a request (``translate.prompt``), which character
cards belong in a prompt (``style``), which locked terms the validator demands (``glossary``) and which entries a cue
matches for the console (``bundle``). They must agree, so they all call :func:`contains`; nothing else in the package
writes a word-boundary pattern (``tests/test_matching.py`` checks it).

Rules:

* Text written without spaces between words (Chinese, Japanese, Thai, Lao, Khmer, Burmese) and Korean, whose
  particles stick to the noun, have no word boundary to look for: a name made of such letters matches anywhere in the
  text.
* Any other name matches as a whole word, case ignored. A name of four letters or more also matches with up to two
  more letters stuck to it, which covers plurals and case endings (``Hobbit`` in ``Hobbits``, ``Gandalf`` in
  ``Gandalfa``). A shorter name stays strict, so ``Tom`` is not found in ``Tommy``.

A pattern is compiled once per name (``lru_cache``); callers that ask about one request pass the request's text
joined (:func:`joined`), so a glossary of a thousand terms costs a thousand searches, not a thousand per line.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

__all__ = ["contains", "contains_any", "joined"]

# Scripts whose words are not separated by spaces, plus Hangul (spaces exist, but particles stick to the noun).
_UNSPACED_RANGES = (
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x1000, 0x109F),  # Burmese
    (0x1780, 0x17FF),  # Khmer
    (0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF),  # Hangul
    (0x2E80, 0x2FDF), (0x3000, 0x303F),  # CJK radicals and punctuation
    (0x3040, 0x30FF), (0x31F0, 0x31FF),  # Hiragana, Katakana
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),  # Han
    (0x20000, 0x2EBEF),  # Han, extensions
)
_UNSPACED = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _UNSPACED_RANGES) + "]")
_MIN_INFLECTED = 4  # a shorter name takes no ending
_ENDING = r"\w{0,2}"


@lru_cache(maxsize=16384)
def _pattern(needle: str) -> re.Pattern[str]:
    escaped = re.escape(needle)
    if _UNSPACED.search(needle):
        return re.compile(escaped, re.IGNORECASE)
    ending = _ENDING if len(needle) >= _MIN_INFLECTED else ""
    return re.compile(rf"(?<!\w){escaped}{ending}(?!\w)", re.IGNORECASE)


def contains(text: str, name: str) -> bool:
    """True when ``text`` mentions ``name`` (see the module notes). An empty name is never mentioned."""
    name = name.strip()
    return bool(name) and _pattern(name).search(text) is not None


def contains_any(text: str, names: Iterable[str]) -> bool:
    return any(contains(text, name) for name in names)


def joined(texts: Iterable[str]) -> str:
    """One haystack for a whole request: a line break never lets a name run from one line into the next."""
    return "\n".join(texts)
