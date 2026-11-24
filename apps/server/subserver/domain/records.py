"""The named types ``Store`` returns for the account, keys, settings and the records of the log and the metrics, and the
translation versions with the story facts (SPEC ADR-8, ADR-12). Plain frozen dataclasses; ``ports`` re-exports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from subcore.memory import Facts


@dataclass(frozen=True)
class Account:
    """The one console account. The hash stays out of ``repr`` so a traceback or log line cannot leak it."""

    user: str
    password_hash: str = field(repr=False)


KEY_OK, KEY_INVALID, KEY_EXHAUSTED = "ok", "invalid", "exhausted"
KEY_STATUSES = (KEY_OK, KEY_INVALID, KEY_EXHAUSTED)


@dataclass(frozen=True)
class ApiKey:
    """One stored API key (FR-20.2). The value stays out of ``repr`` so a traceback or log line cannot leak it."""

    id: int
    provider: str  # gemini | bazarr | tmdb (and the providers P7 adds)
    label: str
    value: str = field(repr=False)
    status: str = KEY_OK
    last_used_at: float | None = None
    updated_at: float = 0.0


@dataclass(frozen=True)
class SettingRow:
    """One stored setting: ``value`` is already decoded from the JSON the table keeps."""

    key: str
    value: Any
    updated_at: float = 0.0


@dataclass(frozen=True)
class JobBundle:
    """The bundle a job's latest translation attempt used (P11, FR-21.2): what "Xuất job spec" hands out."""

    job_id: str
    bundle_hash: str
    bundle_json: str


@dataclass(frozen=True)
class LlmCall:
    """One recorded provider call (NFR-7, FR-19.1). ``at`` is epoch seconds. The fields after ``at`` describe
    how the call was made: ``kind`` is translate | repair | split, ``batch`` the part of the episode, ``retries``
    the attempt number inside one request, ``repairs`` the cues a repair call was asked for, ``cost_est`` the
    estimated price in the currency of the configured per-million-token prices (0 when none are set).
    The hashes are empty until the stage that computes them exists (bundle: P11, style: P12)."""

    id: int
    job_id: str
    provider: str
    model: str
    task: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    outcome: str
    at: float
    kind: str = ""
    prompt_version: str = ""
    bundle_hash: str = ""
    style_hash: str = ""
    glossary_hash: str = ""
    batch: int = 0
    http_status: int | None = None
    finish_reason: str = ""
    retries: int = 0
    repairs: int = 0
    cost_est: float = 0.0
    series_id: int | None = None
    """The series the call was made for, when its job had one (the research budget counts by it, FR-7.6)."""


@dataclass(frozen=True)
class EventRecord:
    """One decision of the system (FR-19.1). ``kind`` is a dotted name (``job.skipped``); ``reason`` says why in a
    sentence an admin can read; ``data`` is the rest, already stripped of secrets. ``at`` is epoch seconds."""

    id: int
    at: float
    level: str
    kind: str
    job_id: str = ""
    unit_id: int | None = None
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualitySignal:
    """What an admin or the validator did to a cue (FR-19.4): ``kind`` is cue_edit | revert | retranslate |
    validate | ab. ``before``/``after`` are the texts either side; ``meta`` carries the rest."""

    id: int
    at: float
    kind: str
    unit_id: int | None = None
    version: int | None = None
    cue: int | None = None
    before: str = ""
    after: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallDay:
    """The model calls of one UTC day, one row per (model, prompt version, task, kind, outcome): what ``metrics_daily``
    is rolled up from (FR-19.5)."""

    day: str
    model: str
    prompt_version: str
    task: str
    kind: str
    outcome: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost: float
    latency_ms: int
    repairs: int


@dataclass(frozen=True)
class JobPrompt:
    """The prompt version a job's model calls used (the first call that names one)."""

    job_id: str
    prompt_version: str


@dataclass(frozen=True)
class MetricRow:
    """One figure of ``metrics_daily``: a count or a sum for a UTC day, split by ``labels`` (model, prompt version...)."""

    day: str
    name: str
    labels: tuple[tuple[str, str], ...]
    value: float


@dataclass(frozen=True)
class BodyRef:
    """Where the prompt and answer of one call are kept (FR-19.2). ``file`` is the segment name without its
    ``.jsonl``/``.jsonl.gz`` ending; ``rotated`` is True once cleanup removed the file (the index outlives it)."""

    call_id: int
    file: str
    offset: int
    length: int
    rotated: bool = False


@dataclass(frozen=True)
class TokenTotals:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


CURRENT = "current"
SUPERSEDED = "superseded"
CANDIDATE = "candidate"  # a result kept but never put on disk until the admin adopts it (FR-16.3, FR-16.4)
REMOVED = "removed"  # tombstone: the admin deleted the target; the content stays for revert (FR-16.6)
PRUNED = "pruned"  # a stored copy removed by retention; the history line stays (FR-13.3, P9)


@dataclass(frozen=True)
class Reclaimed:
    """What one cleanup group removed, or would remove in a dry run (FR-13.4): files, their bytes, database rows."""

    files: int = 0
    bytes: int = 0
    rows: int = 0

    def __add__(self, other: "Reclaimed") -> "Reclaimed":
        return Reclaimed(self.files + other.files, self.bytes + other.bytes, self.rows + other.rows)


@dataclass(frozen=True)
class TranslationVersion:
    """One stored version of a target subtitle file (FR-8.1, FR-16.1). ``status`` is current | superseded |
    candidate | removed | pruned; ``trigger`` is auto | retranslate | revert | external | cue_edit | remove;
    ``origin`` is machine | edit | revert | external | import. ``publishing`` marks a version whose file write has
    been announced but not confirmed yet (FR-16.5). ``missing_units`` lists the earlier units that had no usable
    facts when this was made (``context_complete`` is then False)."""

    output_path: str
    version: int
    stored_path: str
    sha256: str
    trigger: str
    status: str
    created_at: float
    source_path: str = ""
    source_sha256: str = ""
    target_lang: str = ""
    context_hash: str = ""
    parent_version: int | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    unit_id: int | None = None
    glossary_hash: str = ""
    context_complete: bool = True
    memory_hash: str = ""
    missing_units: tuple[str, ...] = ()
    origin: str = ""
    publishing: bool = False
    edited_at: float = 0.0
    job_id: str = ""
    # How the version was made when that is not in its other fields (P12): ``range`` of a retranslated range
    # (FR-11.3.6) and ``variant`` (label, preset, style) of a variant (FR-18.8). Empty for an ordinary one.
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredFacts:
    """Story facts extracted from one source subtitle (FR-5.1). Identified by unit, source hash and
    extraction prompt version; ``created_at`` is when they were saved or last used (``touch``)."""

    unit_id: int
    source_sha256: str
    prompt_version: str
    model: str
    facts: Facts
    facts_hash: str
    tokens_in: int
    tokens_out: int
    created_at: float
