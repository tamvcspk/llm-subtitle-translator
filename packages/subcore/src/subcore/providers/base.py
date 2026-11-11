"""Provider interface (ADR-3).

Adapters turn every vendor failure into one of the typed exceptions below, so
nothing above the provider layer ever parses an error string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Capabilities",
    "LLMRequest",
    "LLMResponse",
    "Provider",
    "ProviderError",
    "AuthError",
    "RateLimited",
    "QuotaExhausted",
    "Unavailable",
    "BadOutput",
]


class ProviderError(Exception):
    """Base class for provider failures."""


class AuthError(ProviderError):
    """Credentials missing, invalid or not permitted. Not retryable."""


class RateLimited(ProviderError):
    """Per-minute style limit hit. Retry after ``retry_after`` seconds if known."""

    def __init__(self, message: str = "rate limited", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class QuotaExhausted(ProviderError):
    """Daily (or longer) quota is gone. Waiting seconds will not help."""

    def __init__(self, message: str = "quota exhausted", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(ProviderError):
    """Transient server-side failure (5xx, timeout, overload)."""

    def __init__(self, message: str = "provider unavailable", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class BadOutput(ProviderError):
    """The call succeeded but produced nothing usable (blocked, empty, truncated)."""


@dataclass(frozen=True)
class Capabilities:
    """What a provider/model can do and its limits (FR-9.2). ``None`` = unknown/unlimited."""

    max_input_tokens: int = 1_000_000
    max_output_tokens: int = 65_536
    structured_output: bool = True
    web_search: bool = False
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None


@dataclass
class LLMRequest:
    """One model call. ``meta`` is for callers/tests and is never sent to the model."""

    system: str
    user: str
    response_schema: dict[str, Any] | None = None
    temperature: float | None = None
    task: str = "translate"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    finish_reason: str = ""


@runtime_checkable
class Provider(Protocol):
    caps: Capabilities
    name: str
    model: str

    def generate(self, req: LLMRequest) -> LLMResponse:
        """Raise RateLimited / QuotaExhausted / Unavailable / BadOutput / AuthError."""
        ...

    def count_tokens(self, req: LLMRequest) -> int: ...
