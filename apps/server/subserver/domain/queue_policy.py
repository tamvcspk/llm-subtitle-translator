"""Pure queue decisions: who goes next, when a provider may be called again, when a daily quota resets.

No clock, no database, no files: every function takes the time it needs, so the tests drive it with a fake
clock (SPEC criterion 14). ``queue.py`` keeps the state; this module only decides.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Collection, Iterable, Mapping

# --- priority (FR-15.7) ---------------------------------------------------------------------------------------
INTERACTIVE, RETRANSLATE, AUTO = 200, 100, 0  # `prio` of a job: the admin just asked, an explicit redo, everything else
AGING_SECONDS = 6 * 3600  # a job that waited this long moves up one step
AGING_STEP = 50
AGING_CAP = 150  # an old automatic job outranks a fresh retranslation (100) but never a fresh interactive one (200)

# --- the breaker (FR-17.3) --------------------------------------------------------------------------------------
BREAKER_THRESHOLD = 3
PROVIDER_RETRY_DELAYS = (300, 900, 1800, 3600)  # how long an open breaker rests, by how many times it has reopened
TRANSIENT_RETRY_SECONDS = 30  # a failure below the threshold: the job goes round again soon
MAX_CRASHES = 3  # a job found running after a restart this many times is failed (FR-15.9)
LEASE_SECONDS = 600

CLOSED, OPEN, HALF_OPEN, EXHAUSTED, INVALID = "closed", "open", "half_open", "exhausted", "invalid"
ALL_MODELS = "*"  # the provider-health row for the provider as a whole (a refused key)

FOREVER = 1e18  # "until someone fixes it": finite, so it survives SQLite and JSON


def priority_class(prio: int) -> str:
    return "interactive" if prio >= INTERACTIVE else "retranslate" if prio >= RETRANSLATE else "auto"


# --- who goes next -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    id: int
    prio: int
    created_at: float
    series_key: str = ""
    tier: str = ""


def effective_priority(prio: int, created_at: float, now: float, aging_seconds: float = AGING_SECONDS) -> int:
    """The class of a job plus what its waiting has earned, so low classes are not starved for ever."""
    steps = int(max(0.0, now - created_at) // aging_seconds) if aging_seconds > 0 else 0
    return prio + min(AGING_CAP, steps * AGING_STEP)


def order_candidates(
    candidates: Iterable[Candidate], last_served: Mapping[str, float], now: float, aging_seconds: float = AGING_SECONDS,
) -> list[Candidate]:
    """Best class first (with aging); inside a class the series served longest ago first, so one series of 150
    episodes does not take every turn; then the oldest job."""
    return sorted(
        candidates,
        key=lambda c: (
            -effective_priority(c.prio, c.created_at, now, aging_seconds),
            last_served.get(c.series_key, 0.0) if c.series_key else 0.0,
            c.created_at,
            c.id,
        ),
    )


# --- models -------------------------------------------------------------------------------------------------------
def configured_models(primary: str, fallback: str = "") -> tuple[str, ...]:
    """Primary model first; a fallback equal to the primary is no fallback."""
    models: list[str] = []
    for model in (str(primary or "").strip(), str(fallback or "").strip()):
        if model and model not in models:
            models.append(model)
    return tuple(models)


def select_model(models: tuple[str, ...], paused: Collection[str], prefer_fallback: bool = False) -> str | None:
    """Pick the model for the next translation attempt, or None when every model is paused."""
    usable = [model for model in models if model not in paused]
    if not usable:
        return None
    if prefer_fallback and len(models) > 1 and models[1] in usable:
        return models[1]
    return usable[0]


def tier_of(models: tuple[str, ...], model: str | None) -> str:
    """``strong`` for the second configured model, else ``light`` (FR-9.5); ``none`` without a model."""
    if not model:
        return "none"
    return "strong" if len(models) > 1 and model == models[1] else "light"


def pick_model(models: tuple[str, ...], paused: Collection[str], tier: str) -> str | None:
    """The model a job of this tier may run on now, or None when it must wait (job needing the strong model
    while only the light one is usable does not block the others, FR-15.7)."""
    if tier == "strong" and len(models) > 1:
        return models[1] if models[1] not in paused else None
    return select_model(models, paused)


# --- provider health: the breaker, the daily quota, a refused key (FR-17) --------------------------------------
@dataclass(frozen=True)
class Health:
    """State of one (provider, model). ``until`` is wall-clock time; ``FOREVER`` means "until someone fixes it"."""

    state: str = CLOSED
    failures: int = 0  # consecutive RateLimited / Unavailable that outlived the retries inside the call
    level: int = 0  # how many times the breaker has reopened without a success in between
    until: float = 0.0
    reason: str = ""


def health_usable(health: Health, now: float) -> bool:
    """May a call be made now? An open breaker whose rest is over allows exactly one trial (see ``begin_trial``)."""
    if health.state == CLOSED:
        return True
    if health.state == HALF_OPEN:
        return False  # the one trial call is out
    if health.state == INVALID:
        return False
    return now >= health.until  # open (trial allowed) or exhausted (the quota has reset)


def begin_trial(health: Health, now: float) -> Health:
    """The scheduler hands the one trial call to a job: from now on nobody else may use the model."""
    if health.state == OPEN and now >= health.until:
        return replace(health, state=HALF_OPEN)
    if health.state == EXHAUSTED and now >= health.until:
        return Health()  # a quota that has reset needs no trial
    return health


def after_success(health: Health) -> Health:
    return Health()


def after_failure(health: Health, now: float, threshold: int = BREAKER_THRESHOLD) -> Health:
    """A call failed with RateLimited or Unavailable after its own retries. Opens the breaker at ``threshold``
    in a row; a failed trial reopens it for the next, longer rest."""
    if health.state == HALF_OPEN:
        level = min(health.level + 1, len(PROVIDER_RETRY_DELAYS) - 1)
        return Health(OPEN, health.failures + 1, level, now + PROVIDER_RETRY_DELAYS[level], "breaker")
    failures = health.failures + 1
    if failures >= max(1, threshold):
        level = min(health.level, len(PROVIDER_RETRY_DELAYS) - 1)
        return Health(OPEN, failures, level, now + PROVIDER_RETRY_DELAYS[level], "breaker")
    return replace(health, failures=failures)


def after_quota(health: Health, reset_at: float) -> Health:
    """The daily quota of the model is spent; it comes back at the provider's reset."""
    return Health(EXHAUSTED, health.failures, health.level, reset_at, "quota")


def after_invalid(health: Health, reason: str) -> Health:
    """A refused key or an unknown model: nothing helps until the admin changes it."""
    return Health(INVALID, health.failures, health.level, FOREVER, reason)


def wait_reason_of(health: Health) -> str:
    """The wait a job shows when this health stops it (FR-15.8)."""
    return "quota" if health.state == EXHAUSTED else "provider"


# --- the daily quota's reset (FR-17.4) ------------------------------------------------------------------------------
# Gemini's daily limits reset at midnight Pacific time (ai.google.dev, rate limits). Not yet checked against the
# live API (SPEC section 8, question 1), so the zone is a setting (`quota_reset_tz`) and a fixed-offset fallback is
# used where the tz database is missing (a slim container, Windows without `tzdata`).
PACIFIC_FALLBACK = timezone(timedelta(hours=-8))


def zone_for(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown zone or no tz database
        return PACIFIC_FALLBACK if name in ("America/Los_Angeles", "US/Pacific") else timezone.utc


def daily_quota_reset_at(now: float, zone: tzinfo | None = None) -> float:
    """The next midnight in ``zone`` (Pacific time by default) after ``now``, as a Unix time."""
    zone = zone or zone_for("America/Los_Angeles")
    local = datetime.fromtimestamp(now, zone)
    midnight = datetime(local.year, local.month, local.day, tzinfo=zone) + timedelta(days=1)
    reset = midnight.timestamp()
    if reset <= now:  # a zone that skips midnight on a DST change
        reset = (midnight + timedelta(days=1)).timestamp()
    return reset


def next_utc_midnight(now: float) -> float:
    """When a per-day call cap (`max_calls_per_day`) counts from zero again."""
    day = datetime.fromtimestamp(now, timezone.utc)
    return (datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)).timestamp()
