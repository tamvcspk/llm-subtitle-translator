"""LLM provider layer: protocol, typed errors, the OpenAI-compatible adapter (Gemini imports lazily), fake adapter."""

from .base import (
    AuthError,
    BadOutput,
    Capabilities,
    DeadlineExceeded,
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
    "CallLog",
    "Capabilities",
    "DeadlineExceeded",
    "FailoverProvider",
    "FakeProvider",
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
