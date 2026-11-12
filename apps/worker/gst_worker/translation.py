"""Failure classes the queue acts on, and secret redaction for anything that reaches logs or job files.

Provider failures arrive from ``subcore`` as typed exceptions and are mapped to these in
``translation_attempt``; nothing here parses error text to classify a failure.
"""

from __future__ import annotations

import re
from typing import Any

ERROR_TAIL_LENGTH = 1000
_KEY_PATTERN = re.compile(r"AIza[A-Za-z0-9_-]+")


class ProviderUnavailableError(RuntimeError):
    """The provider is temporarily unavailable or rate limiting; the queue retries later."""


class DailyQuotaExceededError(RuntimeError):
    """The provider's daily quota is exhausted."""


class HardJobError(RuntimeError):
    """The model's output stayed invalid after repair and splitting.

    A stronger model may still succeed, so the queue escalates the job once before failing it.
    """


def redact(text: str, settings: dict[str, Any] | None = None) -> str:
    """Remove configured API keys and Google key patterns from ``text``."""
    for name in ("gemini_api_key", "gemini_api_key2", "bazarr_api_key", "tmdb_api_key"):
        value = str((settings or {}).get(name) or "")
        if value:
            text = text.replace(value, "[REDACTED]")
    return _KEY_PATTERN.sub("[REDACTED]", text)
