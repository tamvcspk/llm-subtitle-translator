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


class JobFigures(Answer):
    """How the jobs that make translations ended in the window."""

    done: int
    failed: int
    success_rate: float | None
    """Done over done and failed; a cancelled job is neither."""
    retries_per_episode: float | None
    """Times a job waited because the provider said no (429, 503), per episode translated."""


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
    calls_by_task: dict[str, int]
    """Model calls by task: ``translate``, ``extract``, ``judge``, and ``research`` once P16 exists."""
    research_cost: float
    """What the ``research`` task cost; ``0`` when there has been none (needs the prices in Settings)."""
    quota_pauses: int
    """Days of the window on which the daily quota was spent on every model and the queue had to wait for the reset."""
    breaker_opens: int
    jobs: JobFigures


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
    cps_rate: float | None
    """Share of cues the review found over the reading speed."""
    too_long_rate: float | None
    """Share of cues the review found too long."""
    retranslations: int | None
    """Only by model: a retranslation names the model it replaces, not a prompt version (or a pair)."""


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
    episodes: int
    calls: int
    failed: int
    tokens: int
    cost: float
    cues_translated: int
    cue_edits: int


class PreviousRates(Answer):
    """The five rates of Quality over the window just before this one, as long as it: put next to the figures to say better
    or worse. Each is ``null`` when that window has nothing to divide by (or reaches back past a year)."""

    cue_edit_rate: float | None
    repair_rate: float | None
    retranslate_rate: float | None
    cps_rate: float | None
    too_long_rate: float | None


class CorrectionCount(Answer):
    """Cues of one series in one language whose correction still stands: the size of its golden set, over all time."""

    series_id: int
    """The worker's id of the series (``GET /api/library/series``), not the catalog's."""
    lang: str
    edits: int


class Insights(Answer):
    """``GET /api/metrics``."""

    window: Window
    operations: InsightsOperations
    quality: InsightsQuality
    previous: PreviousRates
    by_model: list[InsightsBreakdown]
    by_prompt_version: list[InsightsBreakdown]
    by_model_prompt: list[InsightsBreakdown]
    """One row for each model and prompt version that wrote cues together (both fields set)."""
    ab: list[InsightsAb]
    daily: list[InsightsDay]
    corrections: list[CorrectionCount]
    """A series and language with no correction is not listed: it has none."""


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
