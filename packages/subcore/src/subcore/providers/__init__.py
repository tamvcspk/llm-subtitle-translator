"""LLM provider layer: protocol, typed errors, Gemini and fake adapters."""

from .base import (
    AuthError,
    BadOutput,
    Capabilities,
    DeadlineExceeded,
    LLMRequest,
    LLMResponse,
    Provider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from .fake import FakeProvider
from .limiter import CallLog, FailoverProvider, RateLimiter, ResilientProvider

__all__ = [
    "AuthError",
    "BadOutput",
    "CallLog",
    "Capabilities",
    "DeadlineExceeded",
    "FailoverProvider",
    "FakeProvider",
    "RateLimiter",
    "ResilientProvider",
    "LLMRequest",
    "LLMResponse",
    "Provider",
    "ProviderError",
    "QuotaExhausted",
    "RateLimited",
    "Unavailable",
]
