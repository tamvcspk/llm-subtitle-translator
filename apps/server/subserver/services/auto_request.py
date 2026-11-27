"""The ``auto_request`` policy: requests translations by itself, only where the admin switched it on (FR-15.4).

Off by default. When it is on, globally or for one series (``on`` overrides ``off`` of the global switch and the
other way round), every item Bazarr lists as missing a target language in that scope gets a ``fill`` job with
``origin = policy``, unless it is Ignored, was Removed by the admin, already has a job, lies outside
``GST_ONLY_PATHS`` or the day's ``auto_request_daily_cap`` is spent (what is over stays Missing and is tried
again after the next sync). A series with nothing approved yet gets only its first ``auto_request_bootstrap``
episodes until its glossary is reviewed (``bootstrap.py``, FR-15.12). It runs after a catalog sync, never inside one
(FR-14.1), and uses the same planning as the admin's own requests, so what it did and did not do is readable in the
same reasons.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..domain import queue_policy as policy
from .bootstrap import Bootstrap
from ..domain.config import enabled_source_languages, enabled_target_languages
from .job_requests import JobRequests, Selection
from ..jobs.jobs import FILL
from ..domain.ports import KIND_EPISODE, KIND_MOVIE, KIND_SERIES, CatalogEntry, Telemetry
from ..adapters.store import Store
from ..adapters.telemetry import NULL as NULL_TELEMETRY


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
        self._running = threading.Lock()
        self._last_capped = 0
        self._last_held: dict[str, int] = {}
        self._held: dict[str, int] = {}
        self.bootstrap = Bootstrap(store, settings_source)

    def in_scope(self, series_key: str | None = None) -> list[CatalogEntry]:
        """The items the policy may request: episodes of series it is on for, and movies when it is on globally. With
        ``series_key`` only the episodes of that series (the pass right after the admin let it go, FR-15.12)."""
        everywhere = bool(self._settings().get("auto_request"))
        stored_series = {s.key: s for s in self.store.list_series()}  # one query, not one per series
        if not everywhere and not any(s.auto_request == "on" for s in stored_series.values()):
            self._held = {}
            return []  # the policy is off everywhere: nothing to look at, however big the library (R10-9)
        config = self._config()
        targets = {str(t["code"]).lower() for t in enabled_target_languages(config)}
        sources = enabled_source_languages(config)
        chosen: list[CatalogEntry] = []
        held: dict[str, int] = {}
        for series in self.store.catalog_entries(KIND_SERIES):
            if series_key is not None and series.series_key != series_key:
                continue
            stored = stored_series.get(series.series_key)
            override = stored.auto_request if stored else ""
            if override == "on" or (override != "off" and everywhere):
                episodes = self.store.catalog_entries(KIND_EPISODE, parent_id=series.id)
                wanted = [e for e in episodes if targets & set(e.missing)]
                state = self.bootstrap.state(series, episodes, stored, targets, sources)
                if state.locked:
                    # Nothing is approved yet: only the first episodes now, the rest wait for the glossary (FR-15.12).
                    kept = [e for e in wanted if e.id in state.allowed]
                    if len(kept) < len(wanted):
                        held[series.title or series.series_key] = len(wanted) - len(kept)
                    wanted = kept
                chosen.extend(wanted)
        self._held = held
        if everywhere and series_key is None:
            chosen.extend(m for m in self.store.catalog_entries(KIND_MOVIE) if targets & set(m.missing))
        return chosen

    def target_codes(self) -> set[str]:
        return {str(t["code"]).lower() for t in enabled_target_languages(self._config())}

    def daily_cap(self) -> tuple[int, int | None]:
        """``auto_request_daily_cap`` and how much of it today still has (None when there is no cap)."""
        cap = int(self._settings().get("auto_request_daily_cap") or 0)
        if cap <= 0:
            return cap, None
        return cap, max(0, cap - self.store.policy_jobs_since(policy.next_utc_midnight(self._clock()) - 86_400))

    def bootstrap_view(self, series: CatalogEntry, *, released: bool | None = None) -> tuple[list[CatalogEntry], Any]:
        """The listed episodes of a series and its bootstrap state (``released`` asks "what if it were held again?")."""
        config = self._config()
        episodes = [e for e in self.store.catalog_entries(KIND_EPISODE, parent_id=series.id) if e.gone_at is None]
        state = self.bootstrap.state(
            series, episodes, self.store.series_by_key(series.series_key), self.target_codes(),
            enabled_source_languages(config), released=released,
        )
        return episodes, state

    def run(self, series_key: str | None = None) -> AutoRequested:
        """One pass of the policy. Never two at once: two passes would read ``policy_jobs_since`` together and add up
        past ``auto_request_daily_cap``, so a pass that finds another running does nothing (R10-7). With ``series_key``
        it looks at that series only."""
        if not self._running.acquire(blocking=False):
            return AutoRequested()
        try:
            return self._run(series_key)
        finally:
            self._running.release()

    def start(self, series_key: str, spawn: Callable[[Callable[[], Any]], Any] | None = None) -> bool:
        """The pass for one series, in the background, right after the admin let it go (FR-15.12). The lock is taken here
        so the answer is true: ``False`` means another pass is running now, and this series is asked for by the next
        sync instead. ``spawn`` runs the pass (a thread by default; a test runs it at once)."""
        if not self._running.acquire(blocking=False):
            return False

        def work() -> None:
            try:
                self._run(series_key)
            except Exception:  # noqa: BLE001 - a pass that fails is the next sync's to repeat; it must not kill the thread loudly
                logging.exception("auto_request pass for %s failed", series_key)
            finally:
                self._running.release()

        try:
            (spawn or _in_thread)(work)
        except Exception:  # noqa: BLE001
            self._running.release()
            raise
        return True

    def _run(self, series_key: str | None = None) -> AutoRequested:
        self._held = {}
        entries = self.in_scope(series_key)
        # Said again only when the numbers change, like the cap: the same "waiting" every few minutes is noise.
        if series_key is None and self._held != self._last_held:
            for title, count in self._held.items():
                self.telemetry.event(
                    "policy.bootstrap",
                    reason=f"{title} has no approved glossary yet: {count} episode(s) wait for it to be reviewed (or released)",
                    series=title, held=count,
                )
            self._last_held = dict(self._held)
        if not entries:
            return AutoRequested()
        plan = self.requests.plan(Selection(everything=True), FILL, automatic=True, entries=entries, measure=False)
        cap, limit = self.daily_cap()
        made = self.requests.create(plan, origin="policy", prio=policy.AUTO, limit=limit)
        result = AutoRequested(created=len(made.created), capped=made.capped, considered=len(entries))
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
        if series_key is None:
            self._last_capped = result.capped
        return result


def _in_thread(work: Callable[[], Any]) -> None:
    threading.Thread(target=work, name="auto-request", daemon=True).start()
