"""SRT parsing, serialization and tag validation (FR-1.2, FR-3.2, FR-3.4).

A cue keeps its original ``index`` and raw ``timing`` line so that
serializing a translated cue list reproduces timecodes byte-for-byte. The LLM
never sees either: it only works with the 1-based *ordinal* of a cue in the
parsed list (``i``), which stays stable even when SRT indexes are missing,
duplicated or non-contiguous.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = [
    "Cue",
    "SrtError",
    "parse",
    "parse_file",
    "serialize",
    "write_atomic",
    "tag_problems",
    "line_count_warning",
    "MAX_CUE_LINES",
]

MAX_CUE_LINES = 2

_TIME = r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
_TIMING_RE = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}(?P<rest>.*)$")
_TAG_RE = re.compile(r"<(/?)(i|b|u|font)\b[^>]*>", re.IGNORECASE)


class SrtError(ValueError):
    """The input is not a usable SRT file."""


@dataclass(frozen=True)
class Cue:
    """One subtitle cue.

    ``timing`` is the original timing line (``00:00:01,000 --> 00:00:02,500``,
    including any trailing coordinates) and is what gets written back.
    """

    index: int
    start_ms: int
    end_ms: int
    text: str
    timing: str

    def with_text(self, text: str) -> "Cue":
        return replace(self, text=text)


def _ms(h: str, m: str, s: str, frac: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0"))


def parse(text: str) -> list[Cue]:
    """Parse SRT text. Accepts BOM, CRLF/CR, empty cues and odd indexes."""
    text = text.lstrip(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    timing_at: list[int] = []
    matches: list[re.Match[str]] = []
    for pos, line in enumerate(lines):
        match = _TIMING_RE.match(line)
        if match:
            timing_at.append(pos)
            matches.append(match)
    if not timing_at:
        raise SrtError("no cues found (no 'start --> end' lines)")

    # A cue's index line is the non-blank numeric line directly above its timing line.
    index_at: list[int | None] = []
    for n, pos in enumerate(timing_at):
        floor = timing_at[n - 1] if n else -1
        above = pos - 1
        if above > floor and lines[above].strip().isdigit():
            index_at.append(above)
        else:
            index_at.append(None)

    cues: list[Cue] = []
    for n, pos in enumerate(timing_at):
        if n + 1 < len(timing_at):
            nxt = index_at[n + 1]
            end = nxt if nxt is not None else timing_at[n + 1]
        else:
            end = len(lines)
        body = lines[pos + 1 : end]
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        match = matches[n]
        g = match.groups()
        index_line = index_at[n]
        index = int(lines[index_line].strip()) if index_line is not None else n + 1
        cues.append(
            Cue(
                index=index,
                start_ms=_ms(*g[0:4]),
                end_ms=_ms(*g[4:8]),
                text="\n".join(line.rstrip() for line in body),
                timing=lines[pos].strip(),
            )
        )
    return cues


def parse_file(path: str | os.PathLike[str]) -> list[Cue]:
    """Read and parse an SRT file (UTF-8 with or without BOM, else Windows-1252)."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return parse(text)


def serialize(cues: list[Cue]) -> str:
    """Render cues as SRT text with ``\\n`` line endings and a final newline."""
    blocks = []
    for cue in cues:
        block = f"{cue.index}\n{cue.timing}\n"
        if cue.text:
            block += f"{cue.text}\n"
        blocks.append(block)
    return "\n".join(blocks)


def write_atomic(path: str | os.PathLike[str], text: str) -> None:
    """Write ``text`` as UTF-8 via a temp file in the same directory then rename (FR-3.4)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _tag_counts(text: str) -> Counter[tuple[str, bool]]:
    return Counter((m.group(2).lower(), m.group(1) == "/") for m in _TAG_RE.finditer(text))


def _balanced(text: str) -> bool:
    stack: list[str] = []
    for match in _TAG_RE.finditer(text):
        name = match.group(2).lower()
        if match.group(1):
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def tag_problems(source: str, translated: str) -> list[str]:
    """Return problems with ``<i> <b> <u> <font>`` tags in ``translated`` vs ``source``.

    Tag counts must match the source; if the source is balanced, so must the
    translation be.
    """
    problems: list[str] = []
    src, dst = _tag_counts(source), _tag_counts(translated)
    if src != dst:
        problems.append(
            "tags differ from source (expected "
            + (", ".join(_fmt_tag(k, v) for k, v in sorted(src.items())) or "none")
            + "; got "
            + (", ".join(_fmt_tag(k, v) for k, v in sorted(dst.items())) or "none")
            + ")"
        )
    elif _balanced(source) and not _balanced(translated):
        problems.append("tags are not balanced/nested correctly")
    return problems


def _fmt_tag(key: tuple[str, bool], count: int) -> str:
    name, closing = key
    return f"{count}x<{'/' if closing else ''}{name}>"


def line_count_warning(text: str) -> str | None:
    """FR-3.2: more than two lines in a cue is a warning, never an error."""
    lines = len([line for line in text.split("\n") if line.strip()])
    if lines > MAX_CUE_LINES:
        return f"{lines} lines (recommended max {MAX_CUE_LINES})"
    return None
