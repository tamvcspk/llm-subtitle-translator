"""Use cases of the Library page: read the catalog, sync it now, request translations, ignore, set a series' policy
(FR-14, FR-15.4-15.6). The HTTP layer (``catalog_api``) parses and calls these; nothing here knows about HTTP.
``LookupError`` means "not found" and ``ValueError`` "bad request", as in ``UnitService``.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from . import queue_policy as policy
from .auto_request import AutoRequest
from .catalog import CatalogSync
from .catalog_view import CatalogView
from .job_requests import JobRequests, PlannedJob, RequestPlan, Selection
from .jobs import FILL
from .library import Library
from .ports import KIND_SERIES
from .store import Store

ROWS_SHOWN = 200  # a bulk request over a whole library names its first rows and counts the rest
POLICIES = ("", "on", "off")
IGNORED = "ignored"


def _row(job: PlannedJob) -> dict[str, Any]:
    return {"item_id": job.item_id, "title": job.title, "lang": job.lang, "cues": job.cues, "calls": job.calls,
            "skip": job.skip, "reason": job.reason}


def plan_json(plan: RequestPlan, skipped: tuple[PlannedJob, ...] | None = None) -> dict[str, Any]:
    skipped = plan.skipped if skipped is None else skipped
    return {
        "mode": plan.mode, "estimate": asdict(plan.estimate),
        "planned_total": len(plan.planned), "planned": [_row(j) for j in plan.planned[:ROWS_SHOWN]],
        "skipped_total": len(skipped), "skipped_by_reason": dict(Counter(j.reason for j in skipped)),
        "skipped": [_row(j) for j in skipped[:ROWS_SHOWN]],
    }


class CatalogActions:
    def __init__(
        self, store: Store, library: Library, sync: CatalogSync, auto: AutoRequest, requests: JobRequests, view: CatalogView,
    ) -> None:
        self.store = store
        self.library = library
        self.sync = sync
        self.auto = auto
        self.requests = requests
        self.view = view

    # -- reading ---------------------------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """The catalog's state, with ``shrink`` (``{"listed", "dropped"}``) when the last read was set aside only because
        it listed far fewer items than before: the console offers to accept that read (D3)."""
        shrink = self.sync.shrink
        return {**self.view.status(), "shrink": {"listed": shrink[0], "dropped": shrink[1]} if shrink else None}

    def overview(self) -> dict[str, Any]: return self.view.overview()
    def series_page(self, catalog_id: int) -> dict[str, Any]: return self.view.series_page(catalog_id)
    def movies_page(self) -> dict[str, Any]: return self.view.movies_page()

    # -- acting ----------------------------------------------------------------------------------------------------
    def sync_now(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """**Sync now**: ask the background loop to read the library at once and answer with the state as it is
        (``requested: true``); the read, and the policy after an accepted one, happen in that loop, so this request never
        waits for Bazarr and the policy never runs twice at once (R10-7). ``accept_shrink: true`` lets that one read past
        the shrink guard (D3); the console asks the admin first, saying how many items the read would drop."""
        accept = (body or {}).get("accept_shrink", False)
        if not isinstance(accept, bool):
            raise ValueError("accept_shrink must be true or false")
        self.sync.request(accept_shrink=accept)
        return {**self.status(), "requested": True}

    def request(self, body: dict[str, Any]) -> dict[str, Any]:
        """Ask for translations (FR-15.5). With ``estimate_only`` nothing is made: the plan and what it would cost
        come back, and the console asks the admin to confirm a bulk request before sending it for real."""
        selection = Selection.from_body({**body, "languages": body.get("languages") or ([body["lang"]] if body.get("lang") else None)})
        mode = str(body.get("mode") or FILL)
        plan = self.requests.plan(selection, mode)
        if body.get("estimate_only") is True:
            return {"estimate_only": True, **plan_json(plan)}
        made = self.requests.create(plan, origin="manual", prio=policy.INTERACTIVE)
        return {
            "estimate_only": False, "created": list(made.created), "count": len(made.created), "merged": made.merged,
            **plan_json(plan, made.skipped),
        }

    def ignore(self, body: dict[str, Any], on: bool) -> dict[str, Any]:
        """Ignore (or take back) a decision for the chosen items and languages (FR-15.6)."""
        selection = Selection.from_body({**body, "languages": body.get("languages") or ([body["lang"]] if body.get("lang") else None)})
        langs = self.requests.target_codes(selection.languages)
        changed = 0
        for entry in self.requests.leaves(selection):
            series = self.store.catalog_entry(entry.parent_id) if entry.parent_id else None
            unit_id = self.library.register_entry(entry, series)
            for lang in langs:
                self.store.set_decision(unit_id, lang, IGNORED if on else None)
                changed += 1
        return {"ok": True, "changed": changed, "ignored": on}

    def set_policy(self, catalog_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """``auto_request`` for one series: '' follows the global setting, 'on' and 'off' override it (FR-15.4)."""
        value = body.get("auto_request")
        if value not in POLICIES:
            raise ValueError("auto_request must be '' (follow the global setting), 'on' or 'off'")
        entry = self.store.catalog_entry(catalog_id)
        if entry is None or entry.kind != KIND_SERIES:
            raise LookupError(f"no series {catalog_id} in the catalog")
        series_id = self.store.upsert_series(entry.series_key, title=entry.title, bazarr_id=entry.bazarr_id)
        self.store.set_series_auto_request(series_id, value)
        return {"ok": True, "auto_request": value}

    def set_bootstrap(self, catalog_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """**Continue** (FR-15.12): let ``auto_request`` go past the first episodes of a series whose glossary nobody has
        reviewed. ``released: false`` holds it again. The rest is requested after the next sync, not at once."""
        released = body.get("released", True)
        if not isinstance(released, bool):
            raise ValueError("released must be true or false")
        entry = self.store.catalog_entry(catalog_id)
        if entry is None or entry.kind != KIND_SERIES:
            raise LookupError(f"no series {catalog_id} in the catalog")
        series_id = self.store.upsert_series(entry.series_key, title=entry.title, bazarr_id=entry.bazarr_id)
        self.store.set_series_bootstrap_released(series_id, released)
        return {"ok": True, "released": released}
