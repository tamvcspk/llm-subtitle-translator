"""LLM provider layer: protocol, typed errors, Gemini and fake adapters."""

from .base import (
    AuthError,
    BadOutput,
    Capabilities,
    LLMRequest,
    LLMResponse,
    Provider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from .fake import FakeProvider

__all__ = [
    "AuthError",
    "BadOutput",
    "Capabilities",
    "FakeProvider",
    "LLMRequest",
    "LLMResponse",
    "Provider",
    "ProviderError",
    "QuotaExhausted",
    "RateLimited",
    "Unavailable",
]
