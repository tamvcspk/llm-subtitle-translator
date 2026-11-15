"""What the Library page shows: the catalog with the state of each (item, language) derived on reading (FR-14.4,
FR-14.5, FR-14.6).

There is no column that says "Missing" or "Translated". Three independent axes are read from where they live and put
side by side: the **content** (the catalog says what Bazarr lists, the version history says who made the file), the
**job** (an open job, or the last one failing) and the **decision** (Ignored). A request for the same item and
language is therefore "Missing · Requested", and a translated subtitle that the glossary has since outgrown is
"Translated · Stale". The cheap overview counts only what the catalog and the open jobs say; the full labels are
worked out for one series (or the movies) at a time.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .catalog import pick_source
from .config import enabled_source_languages, enabled_target_languages
from .jobs import CANCELLED, DONE, FAILED, OPEN_STATES, QUEUED, RUNNING, SKIPPED, WAITING, job_key
from .library import Library
from .ports import KIND_EPISODE, KIND_MOVIE, KIND_SERIES, CatalogEntry, Output, Unit
from .queue import JobQueue
from .store import Store
from .subtitles import target_output_path
from .versions import TranslationVersions

# The content axis, the job axis, and the words the console shows for them.
CONTENT_TEXT = {
    "missing": "Missing", "translated": "Translated", "edited": "Edited", "external": "External",
    "outside": "Outside profile",
}
WAIT_TEXT = {"quota": "daily quota", "provider": "provider", "context": "earlier episodes", "settle": "settling", "rate": "busy"}


def display_text(label: dict[str, Any]) -> str:
    parts = [CONTENT_TEXT[label["content"]]]
    if label["removed"]:
        parts.append("Removed")
    if label["stale"]:
        parts.append("Stale")
    job = label["job"]
    if job:
        parts.append({"requested": "Requested", "running": "Running", "failed": "Failed"}.get(job) or
                     f"Waiting · {WAIT_TEXT.get(label['wait'], label['wait'])}")
    if label["ignored"]:
        parts.append("Ignored")
    return " · ".join(parts)


class CatalogView:
    def __init__(
        self,
        store: Store,
        library: Library,
        queue: JobQueue,
        versions: TranslationVersions,
        *,
        config_source: Callable[[], dict[str, Any]],
        settings_source: Callable[[], dict[str, Any]],
    ) -> None:
        self.store = store
        self.library = library
        self.queue = queue
        self.versions = versions
        self._config = config_source
        self._settings = settings_source

    # -- the cache and its age -----------------------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        state = self.store.catalog_state()
        return {
            "synced_at": state.synced_at or None, "attempted_at": state.attempted_at or None, "status": state.status,
            "warning": state.warning, "items": state.items, "listed": self.store.catalog_count(),
            "auto_request": bool(self._settings().get("auto_request")),
            "auto_request_daily_cap": int(self._settings().get("auto_request_daily_cap") or 0),
        }

    # -- the labels ----------------------------------------------------------------------------------------------------
    def label(self, entry: CatalogEntry, lang: str, unit: Unit | None, ignored: set[tuple[int, str]],
              sources: list[dict[str, Any]]) -> dict[str, Any]:
        lang = lang.lower()
        picked = pick_source(entry, sources)
        derived = target_output_path(picked[0].path, lang, str(picked[1]["code"])) if picked else ""
        listed = next((s for s in entry.subtitles if s.code == lang and not s.forced), None)
        stale = removed = False
        if listed is not None:
            content = "external"
            path = listed.path or derived
            if unit is not None and path:
                info = self.library.target_info(unit, Output(unit.id, lang, path))
                if info["status"] != "untranslated" and not info["modified_outside"]:
                    content = "edited" if info["trigger"] in ("cue_edit",) or self._edited(path) else "translated"
                    stale = info["status"] == "stale"
        else:
            removed = bool(derived) and self.versions.latest_status(derived) == "removed"
            content = "missing" if (lang in entry.missing or removed) else "outside"
        job, wait = self._job(entry, unit, lang, picked[0].path if picked else "", derived)
        return {
            "lang": lang, "content": content, "removed": removed, "stale": stale, "job": job, "wait": wait,
            "ignored": unit is not None and (unit.id, lang) in ignored,
        }

    def _edited(self, path: str) -> bool:
        current = self.versions.repo.current_version(path)
        return current is not None and current.origin == "edit"

    def _job(self, entry: CatalogEntry, unit: Unit | None, lang: str, source: str, output: str) -> tuple[str, str]:
        """``(axis value, wait reason)``: requested | running | waiting | failed, or '' when no job says anything."""
        jobs = self.queue.jobs
        open_job = jobs.open_for(job_key(source, output, lang)) if source and output else None
        if open_job is None and unit is not None:
            open_job = jobs.open_for_unit(unit.id, lang)
        if open_job is not None:
            state = open_job["state"]
            if state == RUNNING:
                return "running", ""
            if state == WAITING:
                return "waiting", str(open_job.get("wait_reason") or "")
            return "requested", ""
        last = jobs.latest_for(job_key(source, output, lang), FAILED, DONE, SKIPPED, CANCELLED) if source and output else None
        if last is not None and last["state"] == FAILED:
            return "failed", ""
        return "", ""

    def _targets(self) -> list[str]:
        return [str(t["code"]).lower() for t in enabled_target_languages(self._config())]

    def item(self, entry: CatalogEntry, series: CatalogEntry | None, langs: list[str],
             ignored: set[tuple[int, str]], sources: list[dict[str, Any]]) -> dict[str, Any]:
        unit = self.library.unit_of(entry)
        labels = []
        for lang in langs:
            label = self.label(entry, lang, unit, ignored, sources)
            label["text"] = display_text(label)
            labels.append(label)
        picked = pick_source(entry, sources)
        return {
            "id": entry.id, "kind": entry.kind, "bazarr_id": entry.bazarr_id, "title": entry.title,
            "season": entry.season, "episode": entry.episode, "unit_id": unit.id if unit else None,
            "source": picked[0].code if picked else None, "has_source": picked is not None,
            "gone": entry.gone_at is not None, "targets": labels,
        }

    # -- the pages ---------------------------------------------------------------------------------------------------------
    def overview(self) -> dict[str, Any]:
        """Series (with counts that need no per-item work) and movies, for the Library list."""
        langs = self._targets()
        open_by_series = self._open_by_series()
        out = []
        for series in self.store.catalog_entries(KIND_SERIES):
            episodes = self.store.catalog_entries(KIND_EPISODE, parent_id=series.id)
            counts = {lang: sum(1 for e in episodes if lang in e.missing) for lang in langs}
            stored = self.store.series_by_key(series.series_key)
            out.append({
                "id": series.id, "title": series.title, "key": series.series_key, "episodes": len(episodes),
                "missing": counts, "open_jobs": open_by_series.get(series.bazarr_id, {}),
                "auto_request": stored.auto_request if stored else "", "series_id": stored.id if stored else None,
            })
        movies = self.store.catalog_entries(KIND_MOVIE)
        return {
            "status": self.status(), "languages": langs, "series": out,
            "movies": {"count": len(movies), "missing": {lang: sum(1 for m in movies if lang in m.missing) for lang in langs}},
        }

    def _open_by_series(self) -> dict[str, dict[str, int]]:
        rows = self.store.query(
            "SELECT series_key, state, COUNT(*) AS n FROM jobs WHERE series_key <> '' AND state IN (?, ?, ?)"
            " GROUP BY series_key, state", OPEN_STATES,
        )
        out: dict[str, dict[str, int]] = {}
        for row in rows:
            out.setdefault(str(row["series_key"]), {})[{QUEUED: "requested"}.get(row["state"], row["state"])] = int(row["n"])
        return out

    def series_page(self, catalog_id: int) -> dict[str, Any]:
        series = self.store.catalog_entry(catalog_id)
        if series is None or series.kind != KIND_SERIES:
            raise LookupError(f"no series {catalog_id} in the catalog")
        langs, sources = self._targets(), enabled_source_languages(self._config())
        ignored = self.store.decisions()
        stored = self.store.series_by_key(series.series_key)
        return {
            "status": self.status(), "languages": langs,
            "series": {"id": series.id, "title": series.title, "key": series.series_key,
                       "auto_request": stored.auto_request if stored else "", "series_id": stored.id if stored else None,
                       "gone": series.gone_at is not None},
            "items": [self.item(e, series, langs, ignored, sources)
                      for e in self.store.catalog_entries(KIND_EPISODE, parent_id=series.id, include_gone=True)
                      if e.gone_at is None or self.library.unit_of(e) is not None],
        }

    def movies_page(self) -> dict[str, Any]:
        langs, sources = self._targets(), enabled_source_languages(self._config())
        ignored = self.store.decisions()
        return {
            "status": self.status(), "languages": langs,
            "items": [self.item(m, None, langs, ignored, sources) for m in self.store.catalog_entries(KIND_MOVIE)],
        }
