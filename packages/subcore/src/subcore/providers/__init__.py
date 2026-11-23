"""LLM provider layer: protocol, typed errors, the OpenAI-compatible adapter (Gemini imports lazily), fake adapter."""

from .base import (
    AuthError,
    BadOutput,
    Blocked,
    Capabilities,
    DeadlineExceeded,
    GroundingSource,
    LLMRequest,
    LLMResponse,
    ModelNotFound,
    Provider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from .fake import FakeProvider
from .limiter import CallLog, FailoverProvider, RateLimiter, ResilientProvider
from .openai_compat import OpenAICompatProvider

__all__ = [
    "OpenAICompatProvider",
    "AuthError",
    "BadOutput",
    "Blocked",
    "CallLog",
    "Capabilities",
    "DeadlineExceeded",
    "FailoverProvider",
    "FakeProvider",
    "GroundingSource",
    "RateLimiter",
    "ResilientProvider",
    "LLMRequest",
    "LLMResponse",
    "ModelNotFound",
    "Provider",
    "ProviderError",
    "QuotaExhausted",
    "RateLimited",
    "Unavailable",
]
