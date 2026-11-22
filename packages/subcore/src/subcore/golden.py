"""The golden set of a series: the cues an admin corrected, as the text they should have (FR-19.6b).

A golden file is JSON, not an SRT: it holds only the corrected cues of many episodes, so scoring a configuration against
it looks at exactly those cues and nothing else. ``subcore compare --golden golden.json`` reads it.

```json
{"golden_version": 1, "series": "Arcane", "lang": "vi",
 "cues": [{"season": 1, "episode": 2, "cue": 14, "source": "Mom, wait!", "text": "Má, đợi đã!"}]}
```

A cue counts for an episode only while the source line still says what it said when the admin corrected it: a source
that changed under the cue numbers makes the entry meaningless, so it is left out rather than scored wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from . import srt

__all__ = ["GOLDEN_VERSION", "GoldenCue", "GoldenError", "dumps", "loads", "for_episode"]

GOLDEN_VERSION = 1


class GoldenError(ValueError):
    """A golden file that does not read."""


@dataclass(frozen=True)
class GoldenCue:
    season: int | None
    episode: int | None
    cue: int  # 1-based ordinal in the source subtitle
    source: str
    text: str


def dumps(cues: Sequence[GoldenCue], *, series: str = "", lang: str = "") -> str:
    ordered = sorted(cues, key=lambda c: (c.season if c.season is not None else -1, c.episode or 0, c.cue))
    doc = {
        "golden_version": GOLDEN_VERSION, "series": series, "lang": lang,
        "cues": [
            {"season": c.season, "episode": c.episode, "cue": c.cue, "source": c.source, "text": c.text} for c in ordered
        ],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def _int(value: Any, what: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GoldenError(f"golden: {what} must be a whole number")
    return value


def loads(text: str) -> list[GoldenCue]:
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise GoldenError(f"golden: not JSON ({exc})") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("cues"), list):
        raise GoldenError("golden: expected an object with a 'cues' list")
    version = doc.get("golden_version", GOLDEN_VERSION)
    if version != GOLDEN_VERSION:
        raise GoldenError(f"golden: version {version!r} is not one this tool reads")
    out: list[GoldenCue] = []
    for n, item in enumerate(doc["cues"], start=1):
        if not isinstance(item, dict):
            raise GoldenError(f"golden: entry {n} is not an object")
        cue = _int(item.get("cue"), f"entry {n}: cue")
        if cue is None or cue < 1:
            raise GoldenError(f"golden: entry {n}: cue must be 1 or more")
        if not isinstance(item.get("source"), str) or not isinstance(item.get("text"), str):
            raise GoldenError(f"golden: entry {n}: source and text must be text")
        out.append(GoldenCue(
            season=_int(item.get("season"), f"entry {n}: season", allow_none=True),
            episode=_int(item.get("episode"), f"entry {n}: episode", allow_none=True),
            cue=cue, source=item["source"], text=item["text"],
        ))
    return out


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def for_episode(
    golden: Sequence[GoldenCue], season: int | None, episode: int | None, source: Sequence[srt.Cue]
) -> dict[int, str]:
    """The corrected text by cue number for one episode (``season``/``episode`` as the bundle says, ``None`` for a
    movie), keeping only the entries whose source line is still the one at that number."""
    out: dict[int, str] = {}
    for item in golden:
        if item.season != season or item.episode != episode:
            continue
        if 1 <= item.cue <= len(source) and _same(source[item.cue - 1].text, item.source):
            out[item.cue] = item.text
    return out
