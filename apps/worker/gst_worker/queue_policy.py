"""Pure queue lifecycle decisions."""
from dataclasses import dataclass
from typing import Collection

PROVIDER_RETRY_DELAYS = (300, 900, 1800, 3600)
DAILY_QUOTA_PAUSE_SECONDS = 86_400

@dataclass(frozen=True)
class RetryDecision:
    state: str
    retry_at: float | None = None
    retry_count: int | None = None

def provider_retry_decision(retry_count: int, now: float, fallback_available: bool = False) -> RetryDecision:
    """Back off on the current model, then hand the job to the fallback model once."""
    next_count = retry_count + 1
    if next_count <= len(PROVIDER_RETRY_DELAYS):
        return RetryDecision("deferred", now + PROVIDER_RETRY_DELAYS[next_count - 1], next_count)
    if fallback_available:
        return RetryDecision("fallback")
    return RetryDecision("failed")

def daily_quota_retry_at(now: float) -> float:
    return now + DAILY_QUOTA_PAUSE_SECONDS

def configured_models(primary: str, fallback: str = "") -> tuple[str, ...]:
    """Primary model first; a fallback equal to the primary is no fallback."""
    models: list[str] = []
    for model in (str(primary or "").strip(), str(fallback or "").strip()):
        if model and model not in models:
            models.append(model)
    return tuple(models)

def select_model(models: tuple[str, ...], paused: Collection[str], prefer_fallback: bool = False) -> str | None:
    """Pick the model for the next translation attempt, or None when every model is quota-paused."""
    usable = [model for model in models if model not in paused]
    if not usable:
        return None
    if prefer_fallback and len(models) > 1 and models[1] in usable:
        return models[1]
    return usable[0]
