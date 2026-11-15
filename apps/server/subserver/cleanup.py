"""File cleanup (FR-13): what the worker makes for itself and no longer needs.

One use case, shared by the daily maintenance and the console's "Clean up now" button (and any CLI): ``Cleanup.run``
with ``dry_run=True`` says what would go, group by group, and ``dry_run=False`` does it. The groups (FR-13.1, FR-13.7):

* ``jobs_done`` and ``jobs_failed``: finished rows of the ``jobs`` table older than ``retention_done_days`` and
  ``retention_failed_days``;
* ``versions``: stored copies of older translation versions, beyond ``retention_versions_keep`` (the history lines stay,
  marked ``pruned``);
* ``glossary``: glossary backups beyond ``retention_glossary_keep`` per series and language;
* ``temp``: ``.*.tmp`` files a crash left in ``QUEUE_DIR`` and ``DATA_DIR``, older than a day.

A setting of ``0`` switches its group off. What is never touched (FR-13.2): jobs that are not finished (including
``held``) and refusals, the current version and the first one of each file, facts, summaries and snapshots, the database,
and **every file in a media folder**: the use case reaches version copies, glossary backups and jobs only through their
own stores, and the temporary-file sweep skips every media root. A leftover next to a subtitle in a media folder is only
listed in the report.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import RETENTION_INT_CONFIG_KEYS
from .glossaries import GlossaryFiles
from .jobs import CANCELLED, DONE, FAILED, SKIPPED, JobStore
from .ports import Reclaimed, Telemetry
from .store import Store
from .telemetry import NULL as NULL_TELEMETRY
from .versions import TranslationVersions

SECONDS_PER_DAY = 86400
DAILY_SECONDS = SECONDS_PER_DAY
TEMP_MIN_AGE_SECONDS = SECONDS_PER_DAY  # a temporary file younger than this may still be being written
LAST_RUN_MARK = "_cleanup_last_run"  # a marker row in the settings table; keys that start with "_" are never shown
MEDIA_TEMP_LIMIT = 200  # most leftovers named in a report

JOBS_DONE, JOBS_FAILED, VERSIONS, GLOSSARY, TEMP = "jobs_done", "jobs_failed", "versions", "glossary", "temp"
GROUPS = (JOBS_DONE, JOBS_FAILED, VERSIONS, GLOSSARY, TEMP)
# the setting that switches each group (none for the temporary files: they always go)
SETTING_OF = {
    JOBS_DONE: "retention_done_days", JOBS_FAILED: "retention_failed_days",
    VERSIONS: "retention_versions_keep", GLOSSARY: "retention_glossary_keep",
}


@dataclass(frozen=True)
class GroupReport:
    name: str
    enabled: bool
    window: int | None  # days, or how many to keep; None for the temporary files
    reclaimed: Reclaimed = Reclaimed()
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "enabled": self.enabled, "window": self.window, "files": self.reclaimed.files,
            "rows": self.reclaimed.rows, "bytes": self.reclaimed.bytes, "error": self.error,
        }


@dataclass(frozen=True)
class CleanupReport:
    dry_run: bool
    at: float
    groups: tuple[GroupReport, ...]
    media_temp: tuple[str, ...] = field(default=())  # leftovers beside subtitles: listed, never deleted

    def group(self, name: str) -> GroupReport:
        return next(g for g in self.groups if g.name == name)

    @property
    def total(self) -> Reclaimed:
        return sum((g.reclaimed for g in self.groups), Reclaimed())

    def as_dict(self) -> dict[str, Any]:
        total = self.total
        return {
            "dry_run": self.dry_run, "at": self.at, "groups": [g.as_dict() for g in self.groups],
            "total": {"files": total.files, "rows": total.rows, "bytes": total.bytes},
            "media_temp": list(self.media_temp),
        }


def orphan_temp_files(
    roots: Iterable[str | Path], protected: Iterable[str | Path], *, older_than: float,
) -> tuple[list[Path], list[Path]]:
    """``.*.tmp`` files under ``roots`` last written before ``older_than``: ``(deletable, protected_ones)``.

    A file under any ``protected`` folder (a media root) is returned in the second list and never in the first."""
    guard = [Path(p).resolve() for p in protected if str(p).strip()]
    deletable: list[Path] = []
    kept: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not str(root).strip() or not Path(root).is_dir():
            continue
        for folder, _dirs, names in os.walk(root, followlinks=False):
            for name in names:
                if not (name.startswith(".") and name.endswith(".tmp")):
                    continue
                path = Path(folder, name)
                try:
                    if not path.is_file() or path.is_symlink() or path.stat().st_mtime >= older_than:
                        continue
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                (kept if any(resolved.is_relative_to(g) for g in guard) else deletable).append(path)
    return deletable, kept


class Cleanup:
    def __init__(
        self,
        jobs: JobStore,
        versions: TranslationVersions,
        glossaries: GlossaryFiles,
        store: Store,
        *,
        settings_source: Callable[[], dict[str, Any]],
        temp_roots: Callable[[], Iterable[str]],
        media_roots: Callable[[], Iterable[str]],
        telemetry: Telemetry | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.jobs, self.versions, self.glossaries, self.store = jobs, versions, glossaries, store
        self.settings_source = settings_source
        self.temp_roots = temp_roots
        self.media_roots = media_roots
        self.telemetry: Telemetry = telemetry or NULL_TELEMETRY
        self.clock = clock
        self._lock = threading.Lock()  # the daily run and a click never overlap

    # -- settings ----------------------------------------------------------------------------------------------------
    def window(self, name: str) -> int:
        """The days or the count a group is set to; 0 means the group is off."""
        key = SETTING_OF[name]
        default, minimum = RETENTION_INT_CONFIG_KEYS[key]
        try:
            return max(minimum, int(self.settings_source().get(key, default)))
        except (TypeError, ValueError):
            return default

    # -- the use case ------------------------------------------------------------------------------------------------
    def run(self, *, dry_run: bool = True) -> CleanupReport:
        with self._lock:
            now = self.clock()
            groups: list[GroupReport] = []
            for name in GROUPS[:-1]:
                groups.append(self._group(name, now, dry_run))
            temp, media_temp = self._temp(now, dry_run)
            groups.append(temp)
            report = CleanupReport(dry_run, now, tuple(groups), tuple(media_temp))
        if not dry_run:
            self._record(report)
        return report

    def _group(self, name: str, now: float, dry_run: bool) -> GroupReport:
        window = self.window(name)
        if window <= 0:
            return GroupReport(name, False, window)
        try:
            return GroupReport(name, True, window, self._reclaim(name, window, now, dry_run))
        except Exception as exc:  # noqa: BLE001 - one group failing must not stop the others
            logging.exception("Cleanup of %s failed", name)
            return GroupReport(name, True, window, error=str(exc)[:300])

    def _reclaim(self, name: str, window: int, now: float, dry_run: bool) -> Reclaimed:
        if name in (JOBS_DONE, JOBS_FAILED):
            states = (DONE, SKIPPED, CANCELLED) if name == JOBS_DONE else (FAILED,)
            cutoff = now - window * SECONDS_PER_DAY
            found = self.jobs.expired(states, cutoff)
            if dry_run:
                return found
            return Reclaimed(rows=self.jobs.delete_expired(states, cutoff), bytes=found.bytes)
        if name == VERSIONS:
            return self.versions.prune(window, dry_run=dry_run)
        return self.glossaries.prune_backups(window, dry_run=dry_run)

    def _temp(self, now: float, dry_run: bool) -> tuple[GroupReport, list[str]]:
        try:
            # Media roots, and the folder of every target subtitle even when it is not under a configured root.
            media = [str(p) for p in self.media_roots()]
            folders = sorted({str(Path(p).parent) for p in self.store.output_paths()})
            cutoff = now - TEMP_MIN_AGE_SECONDS
            doomed, guarded = orphan_temp_files(self.temp_roots(), [*media, *folders], older_than=cutoff)
            beside = self._beside_media(folders, cutoff)
            files = size = 0
            for path in doomed:
                try:
                    length = path.stat().st_size
                    if not dry_run:
                        path.unlink()
                except OSError:
                    continue
                files += 1
                size += length
            listed = sorted({str(p) for p in (*guarded, *beside)})[:MEDIA_TEMP_LIMIT]
            return GroupReport(TEMP, True, None, Reclaimed(files=files, bytes=size)), listed
        except Exception as exc:  # noqa: BLE001
            logging.exception("Cleanup of %s failed", TEMP)
            return GroupReport(TEMP, True, None, error=str(exc)[:300]), []

    @staticmethod
    def _beside_media(folders: Iterable[str], older_than: float) -> list[Path]:
        """Temporary files the worker left next to a target subtitle (``.<name>.publish.tmp``): listed, never deleted."""
        found: list[Path] = []
        for folder in folders:
            try:
                for path in Path(folder).glob(".*.publish.tmp"):
                    if path.is_file() and path.stat().st_mtime < older_than:
                        found.append(path)
            except OSError:
                continue
        return found

    # -- the daily run -------------------------------------------------------------------------------------------------
    def run_if_due(self) -> CleanupReport | None:
        """Clean up when a day has passed since the last real run (kept in the database, so a restart does not
        make it run again at once)."""
        now = self.clock()
        last = next((float(r.value) for r in self.store.settings_rows() if r.key == LAST_RUN_MARK), 0.0)
        if last and now - last < DAILY_SECONDS:
            return None
        report = self.run(dry_run=False)
        self.store.setting_save({LAST_RUN_MARK: now})
        return report

    def _record(self, report: CleanupReport) -> None:
        """The summary line of FR-13.5: groups, counts and sizes, never a name or any subtitle text."""
        total = report.total
        parts = ", ".join(f"{g.name}={g.reclaimed.files} files/{g.reclaimed.rows} rows/{g.reclaimed.bytes} B" for g in report.groups if g.enabled)
        logging.info("Cleanup: %s (total %s files, %s rows, %s bytes)", parts or "every group is off", total.files, total.rows, total.bytes)
        self.telemetry.event(
            "cleanup.run", reason="old files and finished jobs were cleaned up by the retention settings",
            groups={g.name: g.as_dict() for g in report.groups}, files=total.files, rows=total.rows, bytes=total.bytes,
            media_leftovers=len(report.media_temp),
        )
