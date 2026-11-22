"""Answers of the routes that are not a page's own (``api/console_api.py``, ``api/auth_api.py``, ``api/keys_api.py``):
who is signed in, the status, the queue, settings, keys, languages, models, logs, events, backups, clean-up and the
connection test."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict

from . import Answer, OpenAnswer, maybe
from .common import Estimate, PlannedRow, ProviderState

# --- who is signed in ------------------------------------------------------------------------------------------------


class MeAnswer(Answer):
    """``GET /api/auth/me``. ``user`` is ``null`` when authentication is off."""

    user: str | None
    via: Literal["session", "basic", "off"]
    can_change_password: bool


class LoginAnswer(Answer):
    """``POST /api/auth/login``: the account that signed in; with authentication off, ``via: "off"`` and no user."""

    ok: bool
    user: str = maybe()
    via: str = maybe()


# --- status and queue ------------------------------------------------------------------------------------------------


class QueueCounts(Answer):
    """Jobs per console state."""

    pending: int
    processing: int
    deferred: int
    done: int
    failed: int
    cancelled: int
    """Jobs the admin cancelled or stopped."""


class ProviderHealth(Answer):
    """How one configured model is doing (FR-17.7)."""

    provider: str
    model: str
    state: ProviderState
    reason: str
    until: float | None
    """Unix seconds when the model may be tried again."""
    failures: int
    usable: bool


class QuotaPause(Answer):
    retry_at: float


class KeyWarning(Answer):
    """What Settings must warn about: ``key-invalid``, ``provider-unconfigured`` or ``model-not-found``."""

    kind: str
    message: str
    provider: str = maybe()
    model: str = maybe()
    key_id: int = maybe()
    label: str = maybe()


class LanguageSetting(Answer):
    code: str
    language: str
    enabled: bool


class SettingsAnswer(Answer):
    """Every setting by its API name (``domain/config.py``; the console's list is ``apps/web/src/lib/settings.ts``).
    Only what has a shape of its own is named: the keys, which are never returned (a stored key is ``**********``) and
    say what they safely can, the languages and the warnings. The other settings are plain values, and the answer
    carries every one of them."""

    model_config = ConfigDict(extra="allow")

    bazarr_url: str
    bazarr_api_key: str
    bazarr_api_key_configured: bool
    bazarr_api_key_last4: str
    bazarr_api_key_status: str
    tmdb_api_key: str
    tmdb_api_key_configured: bool
    tmdb_api_key_last4: str
    tmdb_api_key_status: str
    gemini_api_key: str
    gemini_api_key_configured: bool
    gemini_api_key_last4: str
    gemini_api_key_status: str
    gemini_api_key2: str
    gemini_api_key2_configured: bool
    gemini_api_key2_last4: str
    gemini_api_key2_status: str
    openai_compat_api_key: str
    openai_compat_api_key_configured: bool
    openai_compat_api_key_last4: str
    openai_compat_api_key_status: str
    llm_provider: str
    llm_base_url: str
    source_languages: list[LanguageSetting]
    target_languages: list[LanguageSetting]
    media_roots: list[str]
    key_warnings: list[KeyWarning]


class ModelCount(Answer):
    done: int
    failed: int


class Escalated(Answer):
    total: int
    done: int
    failed: int
    waiting: int


class LayerWindow(Answer):
    """How far back the counts reach, in days; ``0`` means that kind of job is never deleted."""

    done_days: int
    failed_days: int


class ModelLayers(Answer):
    """How the default and the strong model are doing, read from the job rows."""

    by_model: dict[str, ModelCount]
    escalated: Escalated
    window: LayerWindow


class StatusAnswer(Answer):
    """``GET /api/status``."""

    queue: QueueCounts
    daily_quota_pause: QuotaPause | None
    providers: list[ProviderHealth]
    settings: SettingsAnswer
    bazarr_url: str
    model_layers: ModelLayers
    bazarr: dict[str, Any]
    """Bazarr's own answer to a ping, or ``{"error": "..."}`` when it could not be asked."""


class QueueJob(OpenAnswer):
    """One job of ``GET /api/queue``. A job is a stored payload that grows with its kind and its life, so only what a
    screen reads is named."""

    job_id: str
    title: str
    """The series, or the movie; empty for a job made before units existed."""
    label: str
    """``S01E04``; empty for a movie."""
    unit_id: int | None = None
    kind: str | None = None
    """``publish`` (a translation; also when the field is absent), ``variant``, ``extract``, ``suggest`` or ``judge``."""
    version: int | None = None
    """``judge`` jobs: the candidate version being judged."""
    source_code: str | None = None
    target_code: str | None = None
    subtitle_path: str | None = None
    output_path: str | None = None
    origin: str | None = None
    """``manual``, ``policy``, ``retranslate``, ``variant``, ..."""
    mode: str | None = None
    """``fill`` or ``replace``."""
    trigger: str | None = None
    stage: str | None = None
    progress_done: int | None = None
    progress_total: int | None = None
    wait_reason: str | None = None
    """``quota``, ``provider``, ``context``, ``settle`` or ``rate``; waiting jobs only."""
    retry_at: float | None = None
    """Unix seconds, waiting jobs only."""
    attempts: int | None = None
    error: str | None = None
    missing_units: list[str] | None = None
    result: str | None = None
    created_at: float | None = None
    """Unix seconds."""


class QueueAnswer(Answer):
    """``GET /api/queue``: the latest 100 jobs of each state, and the real counts."""

    counts: QueueCounts
    total: int
    daily_quota_pause: QuotaPause | None
    providers: list[ProviderHealth]
    pending: list[QueueJob]
    processing: list[QueueJob]
    deferred: list[QueueJob]
    done: list[QueueJob]
    failed: list[QueueJob]
    cancelled: list[QueueJob]


class StopAnswer(Answer):
    """``POST /api/queue/stop``: ``outcome`` is ``cancelled`` (it had not started) or ``stopping`` (at the next batch)."""

    ok: bool
    outcome: str | None


class RetryManyAnswer(Answer):
    """``POST /api/queue/retry`` for several jobs: ``count`` went back in line, ``skipped`` were left alone."""

    ok: bool
    count: int
    skipped: int


class RetryRow(PlannedRow):
    job_id: str
    skip: str
    reason: str


class RetryEstimate(Answer):
    """``POST /api/queue/retry`` with ``estimate_only``: what retrying would cost."""

    estimate_only: bool
    estimate: Estimate
    planned_total: int
    planned: list[RetryRow]
    skipped_total: int
    skipped_by_reason: dict[str, int]
    skipped: list[RetryRow]


class PrerequisitesRequested(Answer):
    """``POST /api/queue/request-prerequisites``: the jobs made, and the episodes with no source subtitle yet."""

    requested: list[str]
    unavailable: list[str]


class PrerequisitesEstimate(Answer):
    estimate_only: bool
    estimate: Estimate
    planned_total: int
    planned: list[PlannedRow]
    skipped_total: int
    skipped_by_reason: dict[str, int]
    unavailable: list[str]


# --- keys ------------------------------------------------------------------------------------------------------------


class KeyView(Answer):
    """A stored key as the API shows it: no value, only its last four characters (``""`` for a short key)."""

    id: int
    provider: str
    label: str
    status: str
    last4: str
    last_used_at: float | None
    updated_at: float | None


class KeysAnswer(Answer):
    items: list[KeyView]
    warnings: list[KeyWarning]


class ConnectionResult(Answer):
    """The answer of a test of a key or an address; ``auth_failed`` says the service refused the key (not that it was down)."""

    ok: bool
    kind: str
    message: str
    auth_failed: bool = maybe()


class KeyTestAnswer(ConnectionResult):
    key: KeyView = maybe()


# --- languages and models --------------------------------------------------------------------------------------------


class LanguageChoice(Answer):
    code: str
    code3: str
    name: str
    language: str
    enabled_in_bazarr: bool


class LanguagesAnswer(Answer):
    items: list[LanguageChoice]


class ModelChoice(Answer):
    id: str
    name: str


class ModelsAnswer(Answer):
    items: list[ModelChoice]


# --- logs and events -------------------------------------------------------------------------------------------------


class LogEntry(Answer):
    """One event of the worker log; ``details`` holds a traceback."""

    timestamp: str
    level: str
    message: str
    details: str


class LogsAnswer(Answer):
    entries: list[LogEntry]
    truncated: bool
    """Older content was left out."""


class EventRow(Answer):
    id: int
    at: float
    level: str
    kind: str
    job_id: str | None
    unit_id: int | None
    reason: str
    data: dict[str, Any]
    """What the event is about; never a key, a password, a cookie or a header."""


class TelemetryStats(Answer):
    written: int
    dropped: int
    queued: int


class EventsAnswer(Answer):
    """``GET /api/events``: newest first; ``next_before`` is ``null`` on the last page, ``telemetry`` without a database."""

    items: list[EventRow]
    next_before: int | None
    telemetry: TelemetryStats | None


# --- backups and clean-up --------------------------------------------------------------------------------------------


class BackupFile(Answer):
    name: str
    path: str
    size: int
    created_at: int
    """Unix seconds."""


class BackupsAnswer(Answer):
    items: list[BackupFile]


class BackupCreated(Answer):
    backup: BackupFile


class ImportAnswer(Answer):
    """``POST /api/backups/import``: what was restored, the backup taken first, and the zip's own ``backup.json``."""

    ok: bool
    imported: list[str]
    pre_import_backup: BackupFile
    metadata: dict[str, Any]


class CleanupGroup(Answer):
    """One group of the clean-up. ``window`` is days, or how many copies to keep (``null`` for temporary files)."""

    name: str
    enabled: bool
    window: int | None
    files: int
    rows: int
    bytes: int
    error: str


class CleanupTotal(Answer):
    files: int
    rows: int
    bytes: int


class CleanupAnswer(Answer):
    """``POST /api/cleanup``: a preview unless the request said ``dry_run: false``."""

    dry_run: bool
    at: float
    groups: list[CleanupGroup]
    total: CleanupTotal
    media_temp: list[str]
    """Leftover temporary files beside subtitles in media folders: listed, never deleted."""
