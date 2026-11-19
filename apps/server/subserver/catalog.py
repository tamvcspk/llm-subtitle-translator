"""The library as Bazarr lists it, kept as a cache with an age (FR-14, ADR-10).

``CatalogSync`` reads the whole library through the ``MediaLibrary`` port and writes ``catalog_items``. It decides
whether an answer may be applied (FR-14.2): a failed, timed-out, empty or suspiciously smaller answer is set aside,
the cache stays as it was, and the state says so. It never creates a job (FR-14.1): what to translate is decided
by the admin's requests and the ``auto_request`` policy (``requests.py``, ``auto_request.py``), after a sync.

``CatalogPaths`` lets a waiting job find its files again after Sonarr renamed or moved them (FR-14.3): a job names
an item by the library's own id, and the paths it carries are refreshed from the cache when it is claimed.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .library import movie_key_of, series_key_of
from .ports import (
    KIND_EPISODE, KIND_MOVIE, KIND_SERIES, CatalogEntry, CatalogState, CatalogSubtitle, MediaLibrary,
    MediaLibraryError, Telemetry,
)
from .store import Store
from .subtitles import target_output_path
from .telemetry import NULL as NULL_TELEMETRY

DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_SHRINK_GUARD = 20
STATUS_OK, STATUS_KEPT = "ok", "kept"


def with_keys(entries: list[CatalogEntry]) -> list[CatalogEntry]:
    """Give every entry the key its series or movie carries in the worker, so an item and its unit meet."""
    series = {e.bazarr_id: series_key_of(e.tvdb_id, e.bazarr_id, e.title) for e in entries if e.kind == KIND_SERIES}
    out: list[CatalogEntry] = []
    for entry in entries:
        if entry.kind in (KIND_SERIES, KIND_EPISODE):
            sid = entry.bazarr_id if entry.kind == KIND_SERIES else entry.parent_bazarr_id
            key = series.get(sid) or series_key_of(entry.tvdb_id, sid, entry.title)
        else:
            key = movie_key_of(entry.video_path, entry.bazarr_id)
        out.append(dataclasses.replace(entry, series_key=key))
    return out


def pick_source(entry: CatalogEntry, sources: list[dict[str, Any]]) -> tuple[CatalogSubtitle, dict[str, Any]] | None:
    """The source subtitle to translate from: the first configured source language the item has a readable
    (external, not forced) subtitle in. Returns it with the language entry that matched."""
    for source in sources:
        wanted = str(source["code"]).lower()
        for subtitle in entry.subtitles:
            if subtitle.path and not subtitle.forced and subtitle.code == wanted:
                return subtitle, source
    return None


class CatalogSync:
    """Reads the library into the cache, guarded (FR-14.2). ``run`` never raises: the outcome is the state it saves."""

    def __init__(
        self,
        store: Store,
        media: MediaLibrary,
        settings_source: Callable[[], dict[str, Any]],
        telemetry: Telemetry | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.media = media
        self._settings = settings_source
        self.telemetry: Telemetry = telemetry or NULL_TELEMETRY
        self._clock = clock
        self._lock = threading.Lock()
        # Sync now (R10-7): the HTTP handler only asks; the background loop reads. ``_asked`` wakes it at once.
        self._asked = threading.Event()
        self._accept_lock = threading.Lock()
        self._accept_shrink = False
        # ``(items listed, items fewer than before)`` of the last read the guard set aside; None otherwise. In memory
        # only: after a restart the next read says it again.
        self.shrink: tuple[int, int] | None = None

    # -- "Sync now" asks, the background loop reads (R10-7) ------------------------------------------------------
    def request(self, *, accept_shrink: bool = False) -> CatalogState:
        """Ask for a read now and answer at once with the state as it is: the read itself (and the policy after it) is
        done by the background loop, never in the thread of an HTTP request. ``accept_shrink`` lets that one read
        through the shrink guard (D3): the admin saw how many items would go and confirmed."""
        if accept_shrink:
            with self._accept_lock:
                self._accept_shrink = True
        self._asked.set()
        return self.store.catalog_state()

    @property
    def asked(self) -> bool:
        return self._asked.is_set()

    def take_request(self) -> bool:
        """For the loop: was a read asked for since it last looked? (Clears the ask.)"""
        if self._asked.is_set():
            self._asked.clear()
            return True
        return False

    def idle(self, seconds: float) -> None:
        """For the loop: wait, but wake as soon as a read is asked for."""
        self._asked.wait(seconds)

    def interval(self) -> int:
        try:
            return max(0, int(self._settings().get("catalog_sync_interval", DEFAULT_INTERVAL_SECONDS)))
        except (TypeError, ValueError):
            return DEFAULT_INTERVAL_SECONDS

    def due(self) -> bool:
        """Whether the periodic read is overdue; a library never read is always due. 0 turns the periodic read off."""
        interval = self.interval()
        if interval <= 0 or not str(self._settings().get("bazarr_api_key") or "").strip():
            return False
        return self._clock() - self.store.catalog_state().attempted_at >= interval

    def _guard(self) -> int:
        try:
            return min(100, max(0, int(self._settings().get("catalog_shrink_guard", DEFAULT_SHRINK_GUARD))))
        except (TypeError, ValueError):
            return DEFAULT_SHRINK_GUARD

    def _emit(self, kind: str, reason: str, level: str = "INFO", **data: Any) -> None:
        try:
            self.telemetry.event(kind, level=level, reason=reason, **data)
        except Exception:  # noqa: BLE001 - a trace never decides the sync (NFR-11)
            logging.debug("telemetry event %s failed", kind, exc_info=True)

    def run(self) -> CatalogState:
        """Read the library once and apply it when the answer is whole and plausible; otherwise keep the cache.
        A second call while one runs returns the state as it is."""
        if not self._lock.acquire(blocking=False):
            return self.store.catalog_state()
        try:
            with self._accept_lock:
                accept, self._accept_shrink = self._accept_shrink, False
            return self._run(accept_shrink=accept)
        finally:
            self._lock.release()

    def _run(self, *, accept_shrink: bool = False) -> CatalogState:
        now = self._clock()
        before = self.store.catalog_state()
        self.shrink = None
        try:
            entries = with_keys(list(self.media.catalog()))
        except MediaLibraryError as exc:  # the adapter says it in words that carry no address and no key
            return self._kept(before, now, f"{exc} The catalog stays as it was.")
        except Exception as exc:  # noqa: BLE001 - whatever else an adapter raises, the cache must survive it
            return self._kept(before, now, f"Bazarr could not be read ({type(exc).__name__}). The catalog stays as it was.")
        previous = before.items
        if not entries and previous > 0:
            return self._kept(before, now, "Bazarr answered with an empty library; the catalog stays as it was.")
        guard = self._guard()
        if guard and not accept_shrink and previous > 0 and len(entries) < previous * (100 - guard) / 100:
            state = self._kept(
                before, now,
                f"Bazarr listed {len(entries)} items, {previous - len(entries)} fewer than the {previous} before "
                f"(more than {guard}%): the answer is set aside and the catalog stays as it was.",
            )
            self.shrink = (len(entries), previous - len(entries))  # the console offers to accept this read (D3)
            return state
        applied = self.store.catalog_apply(entries, now)
        state = CatalogState(synced_at=now, attempted_at=now, status=STATUS_OK, warning="", items=len(entries))
        self.store.save_catalog_state(state)
        # A read every few minutes that finds nothing new is not news; only a first read, a change or a recovery is.
        if applied.added or applied.gone or not before.synced_at or before.status == STATUS_KEPT:
            self._emit("catalog.synced", "the library was read from Bazarr", items=len(entries), added=applied.added,
                       gone=applied.gone)
        return state

    def _kept(self, before: CatalogState, now: float, warning: str) -> CatalogState:
        state = dataclasses.replace(before, attempted_at=now, status=STATUS_KEPT, warning=warning)
        self.store.save_catalog_state(state)
        logging.warning("Catalog sync set aside: %s", warning)
        self._emit("catalog.kept", warning, level="WARNING", items=before.items, since=before.synced_at or None)
        return state


class CatalogPaths:
    """Refreshes the paths of a job from the cache (FR-14.3), so a file Sonarr renamed after the job was made is
    still found. The library's own id is the identity; the paths are properties looked up when the job runs."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def entry_of(self, job: dict[str, Any]) -> CatalogEntry | None:
        media_id = str(job.get("media_id") or "")
        if not media_id:
            return None
        kind = KIND_EPISODE if str(job.get("media_type") or "") == "series" else KIND_MOVIE
        entry = self.store.catalog_lookup(kind, media_id)
        return entry if entry is not None and entry.gone_at is None else None

    @staticmethod
    def source_of(entry: CatalogEntry, source_code: str) -> CatalogSubtitle | None:
        wanted = source_code.split(":", 1)[0].strip().lower()
        for subtitle in entry.subtitles:
            if subtitle.path and not subtitle.forced and subtitle.code == wanted:
                return subtitle
        return None

    def refresh(self, job: dict[str, Any]) -> bool:
        """Lay the cache's current paths over ``job``; returns whether anything changed. A job the cache does not
        know, or whose source subtitle the cache no longer lists, keeps what it has."""
        entry = self.entry_of(job)
        if entry is None:
            return False
        source_code = str(job.get("source_code") or "")
        source = self.source_of(entry, source_code)
        if source is None:
            return False
        old_subtitle, old_output = str(job.get("subtitle_path") or ""), str(job.get("output_path") or "")
        target = str(job.get("target_code") or "")
        changed: dict[str, str] = {}
        if entry.video_path and entry.video_path != str(job.get("video_path") or ""):
            changed["video_path"] = entry.video_path
        if source.path != old_subtitle:
            changed["subtitle_path"] = source.path
            # The target keeps the name the old source gave it unless the admin's own file sits there.
            if target and (not old_output or old_output == target_output_path(old_subtitle, target, source_code)):
                changed["output_path"] = target_output_path(source.path, target, source_code)
        if not changed:
            return False
        logging.info("Job %s: the library moved its files; following %s", job.get("job_id"), Path(source.path).name)
        job.update(changed)
        unit_id = job.get("unit_id")
        unit = self.store.get_unit(int(unit_id)) if unit_id else None
        if unit is not None:
            self.store.upsert_unit(
                series_id=unit.series_id, movie_key=unit.movie_key, season=unit.season, episode=unit.episode,
                video_path=changed.get("video_path", ""), source_path=changed.get("subtitle_path", ""),
            )
            if "output_path" in changed and target:
                self.store.set_output(unit.id, target, changed["output_path"])
        return True
