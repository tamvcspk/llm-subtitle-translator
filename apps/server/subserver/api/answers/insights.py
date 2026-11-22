"""Answers of Insights and the golden set (``api/insight_api.py``, P15): the figures of FR-19.5 and the corrected cues of a series.

A rate is a number from 0 to 1, or ``null`` when there was nothing to divide by: "no data" is not "0 %"."""
from __future__ import annotations

from pydantic import ConfigDict, Field

from . import Answer, maybe
from .console import ProviderHealth, QueueCounts, QuotaPause


class Window(Answer):
    days: int
    since: float
    until: float


class QueueDepth(Answer):
    counts: QueueCounts
    depth: int
    """Requested + running + waiting."""


class WaitSeconds(Answer):
    jobs: int
    median: float | None
    p90: float | None


class UsageLimits(Answer):
    """``0`` means no limit."""

    rpm: int
    tpm: int
    rpd: int
    max_calls_per_day: int


class Usage(Answer):
    calls_today: int
    calls_last_minute: int
    limits: UsageLimits


class CallFigures(Answer):
    total: int
    by_outcome: dict[str, int]
    failed: int
    rate_429: float | None
    rate_503: float | None


class Escalation(Answer):
    jobs: int
    escalated: int
    rescued: int
    rate: float | None
    rescue_rate: float | None
    window_days: int | None


class PerEpisode(Answer):
    episodes: int
    tokens_in: float | None
    tokens_out: float | None
    cost: float | None


class Tokens(Answer):
    model_config = ConfigDict(populate_by_name=True)

    in_: int = Field(alias="in")
    out: int
    cost: float


class Latency(Answer):
    calls: int
    mean: float | None
    median: float | None
    p90: float | None


class InsightsOperations(Answer):
    queue: QueueDepth
    providers: list[ProviderHealth]
    daily_quota_pause: QuotaPause | None
    wait_seconds: WaitSeconds
    usage: Usage
    calls: CallFigures
    escalation: Escalation
    per_episode: PerEpisode
    tokens: Tokens
    latency_ms: Latency


class Repair(Answer):
    calls: int
    translate_calls: int
    rate: float | None
    cues: int
    cue_rate: float | None


class CueEdits(Answer):
    cues: int
    rate: float | None


class Retranslations(Answer):
    count: int
    rate: float | None
    reverts: int


class Review(Answer):
    by_kind: dict[str, int]
    cps: int
    cps_rate: float | None
    too_long: int
    too_long_rate: float | None


class InsightsQuality(Answer):
    cues_translated: int
    episodes: int
    repair: Repair
    cue_edits: CueEdits
    retranslations: Retranslations
    review: Review
    validate_: dict[str, int] = Field(alias="validate")
    """What the validator saw in a model's reply, by issue."""

    model_config = ConfigDict(populate_by_name=True)


class InsightsBreakdown(Answer):
    """One model, or one prompt version: what it did and how much of it the admin corrected."""

    model: str = maybe()
    prompt_version: str = maybe()
    calls: int
    episodes: int
    cues: int
    tokens_in: int
    tokens_out: int
    cost: float
    repair_rate: float | None
    cue_edits: int
    cue_edit_rate: float | None
    retranslations: int | None
    """Only by model: a retranslation names the model it replaces, not a prompt version."""


class InsightsAb(Answer):
    style: str
    prompt_version: str
    variant: int
    current: int
    tie: int
    judged: int
    score: float | None


class InsightsDay(Answer):
    day: str
    calls: int
    failed: int
    tokens: int
    cost: float
    cues_translated: int
    cue_edits: int


class Insights(Answer):
    """``GET /api/metrics``."""

    window: Window
    operations: InsightsOperations
    quality: InsightsQuality
    by_model: list[InsightsBreakdown]
    by_prompt_version: list[InsightsBreakdown]
    ab: list[InsightsAb]
    daily: list[InsightsDay]


class GoldenCue(Answer):
    season: int | None
    episode: int | None
    cue: int
    source: str
    text: str


class GoldenSet(Answer):
    """``GET /api/series/{id}/golden``: the corrected cues of a series, for ``subcore compare --golden``."""

    golden_version: int
    series: str
    lang: str
    cues: list[GoldenCue]
