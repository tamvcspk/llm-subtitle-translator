"""Bounded log snapshots shared by the console and text clients."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import re
import time
from pathlib import Path

from ..domain.redaction import DEFAULT, Redactor

MAX_LOG_BYTES = 512 * 1024
LOG_HEADER = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) "
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$"
)


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULT_LOG_LEVEL = "INFO"


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


class RedactingFormatter(UTCFormatter):
    """Formats a record, tracebacks included, then removes every secret from the finished text (FR-19.3)."""

    def __init__(self, *args, redactor: Redactor | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.redactor = redactor or DEFAULT

    def format(self, record: logging.LogRecord) -> str:
        return self.redactor.redact(super().format(record))


def normalize_log_level(value: object) -> str:
    """``DEBUG`` | ``INFO`` | ``WARNING`` | ``ERROR``; anything else is the default."""
    name = str(value or "").strip().upper()
    return name if name in LOG_LEVELS else DEFAULT_LOG_LEVEL


def set_log_level(value: object) -> str:
    """Change what the process logs, at once and without a restart (FR-19.3); returns the level in force."""
    name = normalize_log_level(value)
    logging.getLogger().setLevel(getattr(logging, name))
    return name


def read_log_snapshot(log_dir: str, limit: int = 200) -> dict:
    """Read only a bounded tail; keep traceback lines with their parent event."""
    limit = max(1, min(limit, 1000))
    path = Path(log_dir) / "worker.log"
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            offset = max(0, size - MAX_LOG_BYTES)
            stream.seek(offset)
            raw = stream.read(MAX_LOG_BYTES)
    except FileNotFoundError:
        return {"entries": [], "truncated": False}
    if offset:
        raw = raw.partition(b"\n")[2]
    lines = raw.decode("utf-8", errors="replace").splitlines()
    entries: list[dict] = []
    for line in lines:
        match = LOG_HEADER.match(line)
        if match:
            timestamp, level, message = match.groups()
            entries.append({"timestamp": timestamp, "level": level, "message": message, "details": ""})
        elif entries:
            entries[-1]["details"] += line + "\n"
        elif not offset and line.strip():
            entries.append({"timestamp": "", "level": "INFO", "message": line, "details": ""})
    return {
        "entries": entries[-limit:],
        "truncated": offset > 0 or len(entries) > limit,
    }


def configure_logging(log_dir: str, level: object = DEFAULT_LOG_LEVEL) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(Path(log_dir) / "worker.log", maxBytes=5 * 1024 * 1024,
                            backupCount=3, encoding="utf-8"),
    ]
    formatter = RedactingFormatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=getattr(logging, normalize_log_level(level)), handlers=handlers, force=True)


def clear_logs(log_dir: str) -> bool:
    """Truncate the current file under its writer lock; preserve rotated history."""
    path = Path(log_dir) / "worker.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [handler for handler in logging.getLogger().handlers
                if isinstance(handler, logging.FileHandler)
                and Path(handler.baseFilename).resolve() == path.resolve()]
    for handler in handlers:
        handler.acquire()
    try:
        for handler in handlers:
            handler.flush()
        path.write_text("", encoding="utf-8")
    finally:
        for handler in reversed(handlers):
            handler.release()
    return True
