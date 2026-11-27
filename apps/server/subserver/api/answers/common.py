"""Shapes several pages share: the confirmation, the price of a request, the count of what a change would flag stale."""
from __future__ import annotations

from typing import Literal

from . import Answer


class ErrorAnswer(Answer):
    """Every refusal of ``/api/*``: ``400`` for input the worker refuses, ``404`` for an id nobody has, ``409`` for a state
    that forbids the call, ``500`` for anything unexpected (``docs/API.md``, Errors)."""

    error: str


class OkAnswer(Answer):
    """A call that only says whether it did something."""

    ok: bool


class Estimate(Answer):
    """What planned jobs would cost (``services.estimates``): shown in the Estimate/Confirm dialog before anything is queued."""

    jobs: int
    cues: int
    calls: int
    calls_left_today: int | None
    paused_until: float | None
    minutes: float | None
    finish_at: float | None
    extract_calls: int
    """Story-fact reads the jobs would run first (memory on, an episode before the one translated with no facts yet), each episode once for the whole request; 0 when memory is off or the facts are there. Counted apart from ``calls``."""
    extract_model: str
    """The model those reads use (``memory_model``), empty when ``extract_calls`` is 0."""
    summarize_calls: int = 0
    """Calls that condense the old story summaries (memory on, more old episodes than the recent ones kept in full), counted apart from ``calls``; 0 when none."""
    summarize_model: str = ""
    """The model those calls use (``summarize_model``, else ``memory_model``), empty when ``summarize_calls`` is 0."""


class PlannedRow(Answer):
    """One job an estimate would make (or, as a skipped row, would leave out)."""

    title: str
    lang: str
    cues: int
    calls: int


class StyleImpact(Answer):
    """What a change to the rules would do to the translations made without it. Nothing is retranslated."""

    ok: bool
    saved: bool
    stale_now: int
    stale_after: int
    would_stale: int


ProviderState = Literal["closed", "open", "exhausted", "invalid", "half_open"]
