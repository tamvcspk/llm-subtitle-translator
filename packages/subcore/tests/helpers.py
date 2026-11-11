"""Shared test builders. No network, no real subtitle files."""

from __future__ import annotations

import random

from subcore import srt


def fmt_ts(ms: int, sep: str = ",") -> str:
    s, milli = divmod(ms, 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}{sep}{milli:03d}"


def make_cues(texts: list[str], step_ms: int = 2000, dur_ms: int = 1500) -> list[srt.Cue]:
    cues = []
    for n, text in enumerate(texts, start=1):
        start = (n - 1) * step_ms
        end = start + dur_ms
        cues.append(srt.Cue(n, start, end, text, f"{fmt_ts(start)} --> {fmt_ts(end)}"))
    return cues


def numbered(n: int, template: str = "line {n}") -> list[srt.Cue]:
    return make_cues([template.format(n=i) for i in range(1, n + 1)])


_WORDS = "the a night door says never really why not you I we they know see said go come back stay".split()


def synthetic_srt(seed: int) -> str:
    """A reproducible, awkward SRT: BOM/CRLF/empty cues/odd indexes/tags/coordinates."""
    rng = random.Random(seed)
    count = rng.randint(30, 250)
    index = rng.choice([1, 1, 1, 0, 100])
    t = rng.randint(0, 5000)
    blocks = []
    for _ in range(count):
        index += rng.choice([1, 1, 1, 1, 2, 5])  # non-contiguous
        start = t
        end = start + rng.randint(500, 4000)
        t = end + rng.randint(50, 3000)
        sep = rng.choice([",", ",", ","])
        timing = f"{fmt_ts(start, sep)} --> {fmt_ts(end, sep)}"
        if rng.random() < 0.05:
            timing += " X1:100 X2:500 Y1:20 Y2:80"
        kind = rng.random()
        words = lambda: " ".join(rng.choice(_WORDS) for _ in range(rng.randint(2, 8)))  # noqa: E731
        if kind < 0.06:
            text = ""
        elif kind < 0.25:
            text = f"<i>{words()}</i>"
        elif kind < 0.35:
            text = f"<font color=\"#ff0000\">{words()}</font>\n<b>{words()}</b>"
        elif kind < 0.50:
            text = f"- {words()}\n- {words()}"
        elif kind < 0.55:
            text = f"{{\\an8}}{words()}"
        else:
            text = words()
        blocks.append(f"{index}\n{timing}\n{text}\n" if text else f"{index}\n{timing}\n")
    body = "\n".join(blocks)
    if rng.random() < 0.5:
        body = body.replace("\n", "\r\n")
    if rng.random() < 0.5:
        body = chr(0xFEFF) + body
    return body
