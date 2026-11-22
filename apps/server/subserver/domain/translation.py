"""Failure classes the queue acts on, and secret redaction for anything that reaches logs or job files.

Provider failures arrive from ``subcore`` as typed exceptions and are mapped to these in
``translation_attempt``; nothing here parses error text to classify a failure.
"""

from __future__ import annotations

import re
from typing import Any

from .redaction import DEFAULT, REDACTED, SECRET_SETTING_KEYS

ERROR_TAIL_LENGTH = 1000
_KEY_PATTERN = re.compile(r"AIza[A-Za-z0-9_-]+")


class ProviderUnavailableError(RuntimeError):
    """The provider is temporarily unavailable or rate limiting; the queue retries later."""


class DailyQuotaExceededError(RuntimeError):
    """The provider's daily quota is exhausted."""


class ProviderAuthError(RuntimeError):
    """The provider refused the API key (``AuthError``). Not the job's fault: the provider waits for a new key (FR-17.5)."""


class ModelNotFoundError(RuntimeError):
    """The provider does not know the configured model: a setting to fix, not a job to fail (FR-17.5)."""

    def __init__(self, message: str, model: str = "") -> None:
        super().__init__(message)
        self.model = model


class CallBudgetError(RuntimeError):
    """A cap on model calls was reached (FR-17.6): ``scope`` is ``job`` (`max_calls_per_job`) or ``day``."""

    def __init__(self, message: str, scope: str) -> None:
        super().__init__(message)
        self.scope = scope


class JobStopped(Exception):
    """The admin stopped the job, or it was taken away from the worker (recovered, cancelled): end it quietly (FR-15.11)."""


class HardJobError(RuntimeError):
    """The model's output stayed invalid after repair and splitting.

    A stronger model may still succeed, so the queue escalates the job once before failing it.
    """


class ContextGapError(RuntimeError):
    """An earlier episode has no source subtitle yet, so the story memory for this one is incomplete.

    The queue defers the job until ``retry_at``; once ``gap_wait`` has passed the job runs anyway with
    ``context_complete = false`` (FR-5.4). ``since`` is when the wait began and survives in the job file.
    """

    def __init__(
        self, message: str, *, retry_at: float, since: float, missing: list[str], missing_ids: list[int] | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_at = retry_at
        self.since = since
        self.missing = missing
        self.missing_ids = list(missing_ids or [])  # the library units behind ``missing``, for the "request it" button


def redact(text: str, settings: dict[str, Any] | None = None) -> str:
    """Remove the configured API keys, the keys the process registered and every secret shape from ``text``."""
    for name in SECRET_SETTING_KEYS:
        value = str((settings or {}).get(name) or "")
        if value:
            text = text.replace(value, REDACTED)
    return DEFAULT.redact(_KEY_PATTERN.sub(REDACTED, text))
