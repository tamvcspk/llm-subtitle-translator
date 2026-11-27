"""What an action that spends model calls would cost, said before it runs (FR-15.5, UX 6.4).

One ``Estimator`` serves every button that opens the Estimate/Confirm box: a bulk request (``JobRequests``), Retry,
a variant and a retranslation of a range. It only reads: the cues it is given, the day's calls already spent, the quota
pause and how long a call has taken lately. It never calls a model and never writes.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from subcore import srt
from subcore.translate.planner import plan_parts

from ..domain import queue_policy as policy
from ..domain.config import PROVIDER_OPENAI_COMPAT, model_for_task, provider_name
from ..jobs.queue import JobQueue
from ..adapters.store import Store

GEMINI_MAX_OUTPUT_TOKENS = 65_536


@dataclass(frozen=True)
class Estimate:
    """What the planned jobs would cost. ``calls`` is the model calls to translate (a repair or a split adds a few
    more); ``extract_calls`` is the story-fact reads they would run first, on ``extract_model`` (shown apart: they are
    not translation); ``calls_left_today`` is what ``max_calls_per_day`` still allows (None: no cap); ``paused_until`` is when a
    spent quota ends (None: not paused); ``minutes``/``finish_at`` are None until a call has been timed."""

    jobs: int = 0
    cues: int = 0
    calls: int = 0
    calls_left_today: int | None = None
    paused_until: float | None = None
    minutes: float | None = None
    finish_at: float | None = None
    extract_calls: int = 0
    extract_model: str = ""
    # The calls that condense the old story summaries (P19c, FR-5.8): memory work like the reads, on ``summarize_model``.
    summarize_calls: int = 0
    summarize_model: str = ""


class Estimator:
    def __init__(
        self, store: Store, queue: JobQueue, settings_source: Callable[[], dict[str, Any]], *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.queue = queue
        self._settings = settings_source
        self._clock = clock

    def max_output_tokens(self) -> int:
        settings = self._settings()
        if provider_name(settings) == PROVIDER_OPENAI_COMPAT:
            return max(256, int(settings.get("llm_max_output_tokens") or 8_192))
        return GEMINI_MAX_OUTPUT_TOKENS

    def calls_for(self, cues: list[srt.Cue], ordinals: list[int] | None = None) -> int:
        """Model calls to translate these cues (or only the 1-based ``ordinals`` of them): the planner's parts."""
        wanted = ordinals if ordinals is not None else list(range(1, len(cues) + 1))
        return max(1, len(plan_parts(cues, wanted, self.max_output_tokens())))

    def of(self, jobs: int, cues: int, calls: int, extract_calls: int = 0, summarize_calls: int = 0) -> Estimate:
        """``extract_calls`` are the story-fact reads the jobs would run first (P15g) and ``summarize_calls`` the calls
        that condense old story summaries (P19c); they take time like any call."""
        now = self._clock()
        settings = self._settings()
        cap = int(settings.get("max_calls_per_day") or 0)
        left = None
        if cap > 0:
            used = self.store.calls_since(policy.next_utc_midnight(now) - 86_400)
            left = max(0, cap - used)
        average = self.store.average_call_seconds()
        total = calls + extract_calls + summarize_calls
        minutes = total * average / 60.0 if average is not None and total else None
        paused = self.queue.quota_pause_until()
        finish = None
        if minutes is not None:
            finish = max(now, paused or 0.0) + minutes * 60.0
        return Estimate(
            jobs=jobs, cues=cues, calls=calls, calls_left_today=left, paused_until=paused,
            minutes=round(minutes, 1) if minutes is not None else None, finish_at=finish,
            extract_calls=extract_calls, extract_model=model_for_task(settings, "extract") if extract_calls else "",
            summarize_calls=summarize_calls,
            summarize_model=model_for_task(settings, "summarize") if summarize_calls else "",
        )
