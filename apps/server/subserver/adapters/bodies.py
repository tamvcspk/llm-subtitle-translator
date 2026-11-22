"""Prompt and answer bodies kept for analysis, as rotating JSONL files (FR-19.2, ADR-15 point 2).

One call is one line. Lines go to ``<dir>/<YYYY-MM-DD>.<NNN>.jsonl``; a segment closes when the day changes or it
reaches ``segment_bytes``, and a closed segment is gzipped in place (``.jsonl.gz``). The database keeps only
``(call, segment, offset, length)``: offsets are positions in the *uncompressed* stream, so a body can be found
in a segment whether or not it has been compressed since. Cleanup drops whole closed segments, oldest first,
past the age or size limit, and reports which segments went so the index can say "rotated".

Not thread-safe by itself beyond one lock: the telemetry sink is its only writer.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.ports import BodyRef

DEFAULT_BODY_MAX_BYTES = 262_144
MIN_BODY_MAX_BYTES = 1_024
MAX_SEGMENT_BYTES = 16 * 1024 * 1024
_SEGMENT = re.compile(r"^(\d{4}-\d{2}-\d{2})\.(\d{3})\.jsonl(\.gz)?$")
_TEXT_FIELDS = ("system", "user", "response")
_MARK = " …[truncated]"


@dataclass(frozen=True)
class Segment:
    name: str  # without the file ending, e.g. 2026-11-15.001
    day: str
    seq: int
    path: Path
    size: int
    closed: bool  # gzipped


def _utc_day(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%d")


def encode_record(record: dict[str, Any], max_bytes: int) -> bytes:
    """One JSON line no longer than ``max_bytes``: the longest of the text fields are cut until it fits."""
    max_bytes = max(MIN_BODY_MAX_BYTES, int(max_bytes))
    data = dict(record)

    def dump() -> bytes:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")

    line = dump()
    for _ in range(64):
        if len(line) <= max_bytes:
            return line
        data["truncated"] = True
        longest = max((f for f in _TEXT_FIELDS if isinstance(data.get(f), str)), key=lambda f: len(data[f]), default=None)
        if longest is None or not data[longest]:
            break
        excess = len(line) - max_bytes
        keep = max(0, min(len(data[longest]) - excess - len(_MARK) - 16, int(len(data[longest]) * 0.9)))
        data[longest] = data[longest][:keep] + _MARK
        line = dump()
    # Whatever else is in the record is small; this is only reached by a pathological one.
    return json.dumps({"truncated": True, "call": data.get("call")}, separators=(",", ":")).encode("utf-8")


class BodyLog:
    def __init__(self, directory: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = Path(directory)
        self._clock = clock
        self._lock = threading.Lock()
        self._active: Segment | None = None

    # -- segments -------------------------------------------------------------------------
    def segments(self) -> list[Segment]:
        found: list[Segment] = []
        try:
            entries = list(self.directory.iterdir())
        except OSError:
            return []
        for entry in entries:
            match = _SEGMENT.match(entry.name)
            if not match or not entry.is_file():
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            day, seq, gz = match.group(1), int(match.group(2)), bool(match.group(3))
            found.append(Segment(f"{day}.{seq:03d}", day, seq, entry, size, gz))
        return sorted(found, key=lambda s: (s.day, s.seq))

    def total_bytes(self) -> int:
        return sum(s.size for s in self.segments())

    @staticmethod
    def _compress(segment: Segment) -> None:
        target = segment.path.with_name(segment.path.name + ".gz")
        temporary = target.with_name(target.name + ".tmp")
        with segment.path.open("rb") as source, gzip.open(temporary, "wb", compresslevel=6) as out:
            shutil.copyfileobj(source, out)
        temporary.replace(target)
        segment.path.unlink(missing_ok=True)

    def _open_segment(self, day: str, segment_bytes: int) -> Segment:
        """The segment to append to now: today's newest open one if it has room, else a fresh one."""
        self.directory.mkdir(parents=True, exist_ok=True)
        existing = self.segments()
        for segment in existing:  # anything open that is not today's newest is closed first (a crash left it)
            if not segment.closed and (segment.day != day or segment is not existing[-1]):
                self._compress(segment)
        existing = self.segments()
        today = [s for s in existing if s.day == day]
        if today and not today[-1].closed and today[-1].size < segment_bytes:
            return today[-1]
        if today and not today[-1].closed:
            self._compress(today[-1])
        seq = (today[-1].seq + 1) if today else 1
        path = self.directory / f"{day}.{seq:03d}.jsonl"
        return Segment(f"{day}.{seq:03d}", day, seq, path, 0, False)

    # -- write and read ----------------------------------------------------------------------
    def write(self, record: dict[str, Any], *, max_bytes: int = DEFAULT_BODY_MAX_BYTES,
              segment_bytes: int = MAX_SEGMENT_BYTES) -> tuple[str, int, int]:
        """Append one record; returns ``(segment name, offset, length)``. Raises ``OSError`` when the disk refuses."""
        line = encode_record(record, max_bytes)
        with self._lock:
            day = _utc_day(self._clock())
            active = self._active
            if (
                active is None or active.day != day or not active.path.exists()
                or active.path.stat().st_size >= segment_bytes
            ):
                active = self._active = self._open_segment(day, segment_bytes)
            offset = active.path.stat().st_size if active.path.exists() else 0
            with active.path.open("ab") as handle:
                handle.write(line + b"\n")
            return active.name, offset, len(line)

    def read(self, ref: BodyRef) -> dict[str, Any] | None:
        """The record ``ref`` points at, or None when its segment is gone (rotated) or unreadable."""
        for segment in self.segments():
            if segment.name != ref.file:
                continue
            try:
                opener = gzip.open if segment.closed else open
                with opener(segment.path, "rb") as handle:
                    handle.seek(ref.offset)
                    return json.loads(handle.read(ref.length).decode("utf-8"))
            except (OSError, ValueError, EOFError):
                return None
        return None

    # -- cleanup -----------------------------------------------------------------------------
    def close_active(self) -> None:
        """Compress the open segment (called when the process stops, and by tests)."""
        with self._lock:
            self._active = None
            for segment in self.segments():
                if not segment.closed:
                    self._compress(segment)

    def prune(self, *, max_bytes: int = 0, retention_days: int = 0, now: float | None = None) -> list[str]:
        """Delete closed segments older than ``retention_days`` and then the oldest until the total fits in
        ``max_bytes`` (0 turns a limit off). Returns the names removed. The open segment is never deleted here:
        a segment is bounded by ``segment_bytes``, so the size limit is reached through the closed ones."""
        removed: list[str] = []
        with self._lock:
            moment = self._clock() if now is None else now
            segments = self.segments()
            open_names = {s.name for s in segments if not s.closed}
            if retention_days > 0:
                cutoff = _utc_day(moment - retention_days * 86400)
                for segment in segments:
                    if segment.day < cutoff and segment.name not in open_names:
                        removed.append(self._delete(segment))
            remaining = [s for s in self.segments()]
            if max_bytes > 0:
                total = sum(s.size for s in remaining)
                for segment in remaining:
                    if total <= max_bytes:
                        break
                    if segment.name in open_names:
                        continue
                    total -= segment.size
                    removed.append(self._delete(segment))
        return [name for name in removed if name]

    @staticmethod
    def _delete(segment: Segment) -> str:
        try:
            segment.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return ""
        return segment.name


__all__ = ["BodyLog", "Segment", "encode_record", "DEFAULT_BODY_MAX_BYTES", "MAX_SEGMENT_BYTES"]
