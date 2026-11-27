"""Answers of the Library page's routes (``api/catalog_api.py``): what Bazarr lists, the state of each (item, language), and
what asking for translations returns."""
from __future__ import annotations

from typing import Literal

from . import Answer, maybe
from .common import Estimate

#: ``""`` follows the global ``auto_request`` setting; ``on`` and ``off`` override it for one series.
AutoRequestPolicy = Literal["", "on", "off"]
#: The content axis of an (item, language): what the catalog and the version history say about the subtitle.
Content = Literal["missing", "translated", "edited", "external", "outside"]
#: The job axis: an open job, or the last one failing; ``""`` when no job says anything.
JobAxis = Literal["requested", "running", "waiting", "failed", ""]


class Shrink(Answer):
    """The last read was set aside only because it listed far fewer items than before: it can be accepted."""

    listed: int
    dropped: int


class CatalogStatus(Answer):
    """The catalog's age and state. ``status`` is ``ok`` (the last read was applied), ``kept`` (it was set aside and
    ``warning`` says why) or ``""`` (no read yet). ``shrink`` is only in ``GET /api/catalog/status``."""

    synced_at: float | None
    """Unix seconds of the last accepted read of Bazarr, or ``null`` before the first one."""
    attempted_at: float | None
    status: Literal["ok", "kept", ""]
    warning: str
    items: int
    """Items of the last read, and items the catalog lists now."""
    listed: int
    auto_request: bool
    auto_request_daily_cap: int
    shrink: Shrink | None = None


class SyncAnswer(CatalogStatus):
    """``POST /api/catalog/sync``: the state as it is, and that the background loop was asked to read."""

    requested: bool


class JobInfo(Answer):
    """The job a row can act on (Cancel, Stop, Retry) and what it shows about it."""

    job_id: str
    progress_done: int | None
    progress_total: int | None
    retry_at: float | None
    """Unix seconds, waiting jobs only."""
    error: str
    """The last 300 characters of why it failed."""
    missing_units: list[str]
    """``["S01E03"]``: the earlier episodes a ``context`` wait is for."""


class CatalogTarget(Answer):
    """One (item, language): the content axis, the job axis and the decision, with the label the console shows."""

    lang: str
    content: Content
    removed: bool
    stale: bool
    job: JobAxis
    wait: str
    """``quota``, ``provider``, ``context``, ``settle`` or ``rate`` while ``job`` is ``waiting``."""
    ignored: bool
    reason: Literal["", "bootstrap"]
    """Why ``auto_request`` has not asked for this Missing item yet; ``bootstrap`` = its series' glossary awaits review."""
    text: str
    job_info: JobInfo | None


class CatalogItem(Answer):
    id: int
    kind: Literal["series", "episode", "movie"]
    bazarr_id: str
    """Bazarr's own id, as text."""
    title: str
    season: int | None
    episode: int | None
    unit_id: int | None
    """Set once the worker keeps a unit for the item; it opens the Episode or Unit page."""
    source: str | None
    has_source: bool
    gone: bool
    targets: list[CatalogTarget]


class CatalogSeries(Answer):
    id: int
    title: str
    key: str
    episodes: int
    gone: bool
    """Bazarr stopped listing it; the worker still keeps its translations and history."""
    missing: dict[str, int]
    """Counts per target language, ``{"vi": 3}``."""
    open_jobs: dict[str, int]
    """``requested``, ``running``, ``waiting``: open jobs of the series by state."""
    failed: dict[str, int]
    ignored: dict[str, int]
    auto_request: AutoRequestPolicy
    series_id: int | None


class CatalogMovies(Answer):
    count: int
    missing: dict[str, int]
    failed: dict[str, int]
    ignored: dict[str, int]


class CatalogOverview(Answer):
    """``GET /api/catalog``."""

    status: CatalogStatus
    languages: list[str]
    """Enabled target languages, lower case."""
    series: list[CatalogSeries]
    movies: CatalogMovies


class MoviesPage(Answer):
    """``GET /api/catalog/movies``."""

    status: CatalogStatus
    languages: list[str]
    items: list[CatalogItem]


class SeriesBootstrap(Answer):
    """While nothing of a series is approved, ``auto_request`` asks for its first ``limit`` episodes only (FR-15.12).
    ``locked`` says it is holding the rest back; ``pending`` is how many suggestions wait for review."""

    locked: bool
    limit: int
    pending: int
    released: bool
    held: int
    """Episodes with a Missing label and the reason ``bootstrap``, over all target languages; ``held_by_lang`` splits it."""
    held_by_lang: dict[str, int]
    """Per target language: the episodes it holds back (they have a source subtitle and nothing else stops them)."""
    no_source: int
    """Episodes that are Missing but have no source subtitle: not held, only unable to start."""
    glossary_empty: bool
    """Nothing approved yet: no glossary entry and no character card."""
    open_jobs: int
    """Translations of this series that are queued, waiting or running."""
    memory_on: bool
    """Story memory is on for this series, so facts and suggestions get made."""
    queued_policy_jobs: int
    """What "Hold again" would pull back: the policy's jobs that have not started, not counting the bootstrap's own episodes."""
    relockable: bool
    """The series is released, the policy is on for it and holding it again would hold something (otherwise Hold again does nothing)."""


class SeriesHeader(Answer):
    id: int
    title: str
    key: str
    auto_request: AutoRequestPolicy
    series_id: int | None
    gone: bool
    bootstrap: SeriesBootstrap


class SeriesPage(Answer):
    """``GET /api/catalog/series/{id}``: one series with the label of every episode and language."""

    status: CatalogStatus
    languages: list[str]
    series: SeriesHeader
    items: list[CatalogItem]


class PolicyAnswer(Answer):
    ok: bool
    auto_request: AutoRequestPolicy


class BootstrapAnswer(Answer):
    """``PUT /api/catalog/series/{id}/bootstrap``. A real release says ``started``; a hold says ``cancelled``; an estimate says what
    letting the series go would ask for (and nothing changed)."""

    ok: bool
    released: bool
    estimate_only: bool = maybe()
    started: bool = maybe()
    """Release only: the policy's pass for this series was started now. False: another pass was running, the next sync asks."""
    cancelled: int = maybe()
    """Hold again only: how many jobs of the policy that had not started were pulled back."""
    estimate: Estimate = maybe()
    """Estimate only: what the episodes the policy holds back would cost (the same box as a bulk request)."""
    planned_total: int = maybe()
    skipped_total: int = maybe()
    skipped_by_reason: dict[str, int] = maybe()
    glossary_empty: bool = maybe()
    """Estimate only: no glossary entry and no character card yet; the box warns that the rest would be translated without them."""
    daily_cap: int = maybe()
    cap_left: int | None = maybe()
    """Estimate only: ``auto_request_daily_cap`` (0: none) and what is left of it today; the policy asks for no more than that."""


class IgnoreAnswer(Answer):
    ok: bool
    changed: int
    ignored: bool


class JobRow(Answer):
    """One item a request would make a job for (or, among the skipped, would leave out and ``reason`` says why)."""

    item_id: int
    title: str
    lang: str
    cues: int
    calls: int
    skip: str
    reason: str
    action: Literal["translate", "retranslate", ""] = ""
    """``translate`` (new) or ``retranslate`` (overwrite with version backup) for a planned row; ``""`` when skipped."""


class JobsAnswer(Answer):
    """``POST /api/jobs``: the plan and what it costs; a real request adds ``created``, ``count`` and ``merged``."""

    estimate_only: bool
    mode: str
    estimate: Estimate
    planned_total: int
    translate_total: int = 0
    """How many planned jobs translate an episode or movie that has no target subtitle yet."""
    retranslate_total: int = 0
    """How many planned jobs retranslate an existing target subtitle (keeping the previous file as a version first)."""
    planned: list[JobRow]
    """The first 200 rows of each list; the totals count them all."""
    skipped_total: int
    skipped_by_reason: dict[str, int]
    skipped: list[JobRow]
    created: list[str] = maybe()
    """A real request only: the jobs made, how many, and how many open ``fill`` jobs were turned into the replacement."""
    count: int = maybe()
    merged: int = maybe()
