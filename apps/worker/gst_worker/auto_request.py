"""The ``auto_request`` policy: requests translations by itself, only where the admin switched it on (FR-15.4).

Off by default. When it is on, globally or for one series (``on`` overrides ``off`` of the global switch and the
other way round), every item Bazarr lists as missing a target language in that scope gets a ``fill`` job with
``origin = policy``, unless it is Ignored, was Removed by the admin, already has a job, lies outside
``GST_ONLY_PATHS`` or the day's ``auto_request_daily_cap`` is spent (what is over stays Missing and is tried
again after the next sync). It runs after a catalog sync, never inside one (FR-14.1), and uses the same planning
as the admin's own requests, so what it did and did not do is readable in the same reasons.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import queue_policy as policy
from .config import enabled_target_languages
from .job_requests import JobRequests, Selection
from .jobs import FILL
from .ports import KIND_EPISODE, KIND_MOVIE, KIND_SERIES, CatalogEntry, Telemetry
from .store import Store
from .telemetry import NULL as NULL_TELEMETRY


@dataclass(frozen=True)
class AutoRequested:
    """What one run did: jobs created, items left Missing because the day's cap was spent, items looked at."""

    created: int = 0
    capped: int = 0
    considered: int = 0


def sync_and_request(sync: Any, auto: "AutoRequest") -> tuple[Any, AutoRequested | None]:
    """One cycle: read the library, and only if the answer was applied, let the policy ask for what is missing.
    A read that was set aside leaves the policy alone: it must not act on a catalog it has just refused to trust."""
    state = sync.run()
    return state, (auto.run() if state.status == "ok" else None)


class AutoRequest:
    def __init__(
        self,
        store: Store,
        requests: JobRequests,
        settings_source: Callable[[], dict[str, Any]],
        config_source: Callable[[], dict[str, Any]],
        telemetry: Telemetry | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.requests = requests
        self._settings = settings_source
        self._config = config_source
        self.telemetry: Telemetry = telemetry or NULL_TELEMETRY
        self._clock = clock
        self._last_capped = 0

    def in_scope(self) -> list[CatalogEntry]:
        """The items the policy may request: episodes of series it is on for, and movies when it is on globally."""
        everywhere = bool(self._settings().get("auto_request"))
        targets = {str(t["code"]).lower() for t in enabled_target_languages(self._config())}
        chosen: list[CatalogEntry] = []
        for series in self.store.catalog_entries(KIND_SERIES):
            stored = self.store.series_by_key(series.series_key)
            override = stored.auto_request if stored else ""
            if override == "on" or (override != "off" and everywhere):
                chosen.extend(e for e in self.store.catalog_entries(KIND_EPISODE, parent_id=series.id)
                              if targets & set(e.missing))
        if everywhere:
            chosen.extend(m for m in self.store.catalog_entries(KIND_MOVIE) if targets & set(m.missing))
        return chosen

    def run(self) -> AutoRequested:
        entries = self.in_scope()
        if not entries:
            return AutoRequested()
        plan = self.requests.plan(Selection(everything=True), FILL, automatic=True, entries=entries, measure=False)
        cap = int(self._settings().get("auto_request_daily_cap") or 0)
        limit = None
        if cap > 0:
            used = self.store.policy_jobs_since(policy.next_utc_midnight(self._clock()) - 86_400)
            limit = max(0, cap - used)
        made = self.requests.create(plan, origin="policy", prio=policy.AUTO, limit=limit)
        capped = max(0, len(plan.planned) - len(made.created) - made.merged) if limit is not None else 0
        result = AutoRequested(created=len(made.created), capped=capped, considered=len(entries))
        if result.created:
            self.telemetry.event(
                "policy.requested", reason=f"auto_request asked for {result.created} translation(s) of items Bazarr lists as missing",
                jobs=result.created, considered=result.considered,
            )
        # The same "N stay Missing" every few minutes is noise; say it again only when the number changes.
        if result.capped and result.capped != self._last_capped:
            self.telemetry.event(
                "policy.capped", level="WARNING",
                reason=f"auto_request_daily_cap={cap} is spent; {result.capped} item(s) stay Missing until tomorrow",
                capped=result.capped,
            )
        self._last_capped = result.capped
        return result
