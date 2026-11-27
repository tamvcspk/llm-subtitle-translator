"""The request bodies of the console's routes (ADR-18, P15d step 2).

Every model lets unknown fields through (``extra="allow"``): the console has always forwarded the whole JSON object to a
use case, which reads what it needs and says what is wrong in its own words, and a field this file does not name must not
start to fail. What a model adds is the shape of the fields the *API layer itself* reads, checked before the use case runs and
written into the OpenAPI document.

A flag that makes the call spend money (``estimate_only``, ``dry_run`` of a bulk call) is ``StrictBool``: ``"yes"`` and ``1`` are
not a yes, and a client that sends one now gets a 400 instead of a call that was silently not an estimate. The two flags whose safe
side is the default (``dry_run`` of the clean-up, ``include_keys`` of a backup) keep the rule they always had: only the exact value
changes the default, anything else is the safe side. A body that is not a JSON object is refused.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class RequestObject(BaseModel):
    """Any JSON object. The route hands it to a use case that validates it (a glossary, a style, a correction, a setting)."""

    model_config = ConfigDict(extra="allow")


class LoginBody(RequestObject):
    user: str = ""
    password: str = ""


class PasswordBody(RequestObject):
    current: str = ""
    new: str = ""


class JobBody(RequestObject):
    """The queue buttons that act on one job."""

    job_id: str = ""


class RetryBody(RequestObject):
    """Retry one job (``job_id``), a list of them (``job_ids``) or every failed one (``all``); ``estimate_only`` says the price."""

    job_id: str = ""
    job_ids: list[str] | None = None
    all: StrictBool | None = None
    estimate_only: StrictBool | None = None


class PrerequisitesBody(RequestObject):
    job_id: str = ""
    estimate_only: StrictBool = False


class DeleteJobBody(RequestObject):
    state: str = ""
    job_id: str = ""


class BackupBody(RequestObject):
    """Keys go into a manual backup only when ``include_keys`` is exactly ``true``; anything else reads as no."""

    include_keys: Any = Field(False, json_schema_extra={"type": "boolean"})


class CleanupBody(RequestObject):
    """A preview unless ``dry_run`` is exactly ``false`` (FR-13.4): any other value reads as ``true``, the side that deletes nothing."""

    dry_run: Any = Field(True, json_schema_extra={"type": "boolean"})


class ConnectionBody(RequestObject):
    """``kind`` names what to test; the key typed in the form comes in the field of that name (``gemini_api_key``, ...)."""

    kind: str = ""


class KeyBody(RequestObject):
    provider: str | None = None
    value: str | None = None
    label: str | None = None
    status: str | None = None


class SyncBody(RequestObject):
    accept_shrink: StrictBool = False


class PolicyBody(RequestObject):
    auto_request: Any = None


class BootstrapBody(RequestObject):
    """``released: true`` lets a held series go (with ``estimate_only`` it only says what that would ask for); ``false`` holds it
    again, and ``cancel_queued_policy`` then also pulls back the policy's jobs that have not started (FR-15.12)."""

    released: StrictBool = True
    estimate_only: StrictBool = False
    cancel_queued_policy: StrictBool = False


class SelectionBody(RequestObject):
    """What to request or ignore in the Library: exactly one of ``items``, ``series`` or ``all`` (the use case says so)."""

    items: list[Any] | None = None
    series: int | str | None = None
    season: int | str | None = None
    all: StrictBool | None = None
    languages: list[Any] | None = None
    lang: str | None = None
    mode: str | None = None
    estimate_only: StrictBool | None = None


class BulkBody(RequestObject):
    series_id: int | str | None = None
    lang: str | None = None
    expect: Any = None
    dry_run: StrictBool | None = None


class GenerateBody(RequestObject):
    series_id: int | str | None = None
    lang: str | None = None
    estimate_only: StrictBool | None = None


class ResearchBody(GenerateBody):
    """``POST /api/suggestions/research``: ``auto_accept`` is the Estimate box's tick for this run only (absent: what
    ``auto_accept_terms`` says)."""

    auto_accept: StrictBool | None = None


class AcceptBody(RequestObject):
    payload: dict[str, Any] | None = None
    dry_run: StrictBool | None = None


class DryRunBody(RequestObject):
    dry_run: StrictBool | None = None


class GlossarySaveBody(RequestObject):
    """Save a glossary as a document or as YAML; ``dry_run`` counts what it would flag stale and saves nothing."""

    lang: str | None = None
    doc: dict[str, Any] | None = None
    yaml: str | None = None
    dry_run: StrictBool | None = None


class GlossaryRestoreBody(RequestObject):
    lang: str | None = None
    version: int | str | None = None
    dry_run: StrictBool | None = None


class GlossaryCopyBody(RequestObject):
    lang: str | None = None
    from_series: int | str | None = None
    merge: StrictBool | None = None
    dry_run: StrictBool | None = None


class AdoptBody(RequestObject):
    lang: str | None = None
    apply_style: StrictBool | None = None
    dry_run: StrictBool | None = None


class EstimateBody(RequestObject):
    """A request that spends model calls (retranslate, a variant): ``estimate_only`` is the price, so a client that sends
    ``"true"`` or ``1`` gets a 400, not a job that was queued for real."""

    estimate_only: StrictBool | None = None


class JudgeBody(EstimateBody):
    again: StrictBool | None = None
