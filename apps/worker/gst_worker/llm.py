"""Build the model provider for one translation attempt from worker settings.

Each configured Gemini API key becomes its own rate-limited provider (limits are per key/project);
the keys are tried in order and the next one takes over when one is spent (FR-9.4).
"""

from __future__ import annotations

from typing import Any, Callable

from subcore.providers import (
    CallLog,
    FailoverProvider,
    Provider,
    RateLimiter,
    ResilientProvider,
)

# Free-tier default; paid projects set LLM_RPM=0 (unlimited) or their own number.
FREE_TIER_RPM = 10


class MissingApiKey(RuntimeError):
    """No Gemini API key is configured."""


def _int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def rate_limits(settings: dict[str, Any]) -> tuple[int, int, int]:
    """(rpm, tpm, rpd); 0 means unlimited."""
    default_rpm = 0 if settings.get("gst_paid_quota") else FREE_TIER_RPM
    return (
        _int(settings.get("llm_rpm"), default_rpm),
        _int(settings.get("llm_tpm"), 0),
        _int(settings.get("llm_rpd"), 0),
    )


def thinking_budget(settings: dict[str, Any], model: str) -> int | None:
    """Gemini 2.5 takes a token budget; newer models are left on their own default."""
    if "2.5" not in model:
        return None
    if settings.get("gst_no_thinking"):
        return 0
    try:
        return int(str(settings.get("gst_thinking_budget") or "").strip())
    except ValueError:
        return None


def build_provider(
    settings: dict[str, Any],
    model: str,
    *,
    deadline: float | None = None,
    on_call: Callable[[CallLog], None] | None = None,
) -> Provider:
    from subcore.providers.gemini import GeminiProvider

    keys = [str(settings.get(name) or "").strip() for name in ("gemini_api_key", "gemini_api_key2")]
    keys = [key for key in keys if key]
    if not keys:
        raise MissingApiKey("Gemini API key is not configured")
    rpm, tpm, rpd = rate_limits(settings)
    budget = thinking_budget(settings, model)
    providers: list[Provider] = []
    for key in keys:
        inner = GeminiProvider(key, model, thinking_budget=budget)
        providers.append(
            ResilientProvider(inner, RateLimiter(rpm, tpm, rpd), deadline=deadline, on_call=on_call)
        )
    return providers[0] if len(providers) == 1 else FailoverProvider(providers)
