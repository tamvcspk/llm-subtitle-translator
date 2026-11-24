"""Asking for translations: choose items from the catalog, say what it would cost, then make the jobs (FR-14.4,
FR-15.2, FR-15.5, FR-15.6).

A "request" is not a thing of its own (SPEC decision 13): asking is making a job. ``plan`` decides, for every
(item, language) the admin chose, whether a job would be made and why not when it would not; ``estimate`` says what
the jobs would cost (a bulk request is confirmed against it, FR-15.5); ``create`` makes them. The ``auto_request``
policy (``auto_request.py``) uses the same planning with ``origin = policy``, so what it does can be read off the
same reasons. Only the console and the policy call this; nothing here talks to Bazarr (the catalog is a cache).
"""
from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from subcore import srt

from ..domain import queue_policy as policy
from .catalog import pick_source
from ..domain.config import enabled_source_languages, enabled_target_languages
from .console import CONSOLE_PROVIDER
from .estimates import Estimate, Estimator
from ..jobs.jobs import CANCELLED, FAILED, FILL, REPLACE, job_key
from .library import Library
from .memory import StoryMemory
from ..domain.ports import KIND_EPISODE, KIND_MOVIE, KIND_SERIES, CatalogEntry, Telemetry, Unit
from ..jobs.queue import JobQueue, outside_allowed_paths
from ..adapters.store import Store
from ..domain.subtitles import existing_target_subtitle, target_output_path
from ..adapters.telemetry import NULL as NULL_TELEMETRY
from .versions import TranslationVersions

AUTO_MODE = "auto"

REASONS = {
    "ignored": "ignored by the admin",
    "same-language": "the source and the target language are the same",
    "no-source": "no source subtitle in a configured source language",
    "source-missing": "the source subtitle is not on disk",
    "unreadable": "the source subtitle cannot be read",
    "has-target": "the item already has a subtitle in this language",
    "requested": "already requested",
    "not-missing": "Bazarr does not list this language as missing",
    "removed": "the admin removed this subtitle; only a manual request brings it back",
    "outside-paths": "outside GST_ONLY_PATHS",
    "failed": "an earlier job for this item failed; only a manual request tries again",
    "cancelled": "the admin cancelled the job for this item; only a manual request asks again",
}


@dataclass(frozen=True)
class Selection:
    """What a request is about: named catalog items, a whole series, one season of it, or everything; optionally
    only some target languages (default: every enabled one)."""

    items: tuple[int, ...] = ()
    series: int | None = None
    season: int | None = None
    everything: bool = False
    languages: tuple[str, ...] = ()

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> "Selection":
        def number(value: Any, what: str) -> int | None:
            if value in (None, ""):
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{what} must be a number") from None

        items = body.get("items")
        if items is not None and (not isinstance(items, list) or not all(isinstance(i, int) for i in items)):
            raise ValueError("items must be a list of catalog item ids")
        languages = body.get("languages")
        if languages is not None and (not isinstance(languages, list) or not all(isinstance(i, str) for i in languages)):
            raise ValueError("languages must be a list of language codes")
        selection = cls(
            items=tuple(items or ()), series=number(body.get("series"), "series"),
            season=number(body.get("season"), "season"), everything=body.get("all") is True,
            languages=tuple(str(code).lower() for code in languages or ()),
        )
        if sum([bool(selection.items), selection.series is not None, selection.everything]) != 1:
            raise ValueError("Name what to request with exactly one of items, series or all")
        if selection.season is not None and selection.series is None:
            raise ValueError("season needs a series")
        return selection


@dataclass(frozen=True)
class PlannedJob:
    """One (item, language) of a request. ``skip`` is a key of ``REASONS`` when no job would be made, else ''.
    ``action`` is ``translate`` (fill) or ``retranslate`` (replace) for a planned job, and '' when skipped."""

    item_id: int
    title: str
    lang: str
    language: str = ""
    skip: str = ""
    action: str = ""
    source_path: str = ""
    output_path: str = ""
    source_code: str = ""
    source_language: str = ""
    cues: int = 0
    calls: int = 0

    @property
    def reason(self) -> str:
        return REASONS.get(self.skip, self.skip)


@dataclass(frozen=True)
class RequestPlan:
    planned: tuple[PlannedJob, ...] = ()
    skipped: tuple[PlannedJob, ...] = ()
    estimate: Estimate = field(default_factory=Estimate)
    mode: str = FILL


@dataclass(frozen=True)
class Requested:
    created: tuple[str, ...] = ()
    merged: int = 0  # asked again for something already open: lifted, not duplicated
    skipped: tuple[PlannedJob, ...] = ()
    capped: int = 0  # planned jobs left unmade because ``limit`` was reached (and only for that reason)


class JobRequests:
    def __init__(
        self,
        store: Store,
        library: Library,
        queue: JobQueue,
        versions: TranslationVersions,
        *,
        config_source: Callable[[], dict[str, Any]],
        settings_source: Callable[[], dict[str, Any]],
        telemetry: Telemetry | None = None,
        clock: Callable[[], float] = time.time,
        estimator: Estimator | None = None,
        memory: StoryMemory | None = None,
    ) -> None:
        self.store = store
        self.memory = memory  # when set, a request's estimate also counts the fact reads its jobs would run (P15g)
        self.library = library
        self.queue = queue
        self.versions = versions
        self._config = config_source
        self._settings = settings_source
        self.telemetry: Telemetry = telemetry or NULL_TELEMETRY
        self._clock = clock
        self.estimator = estimator or Estimator(store, queue, settings_source, clock=clock)

    # -- what the selection names ----------------------------------------------------------------------------------
    def leaves(self, selection: Selection) -> list[CatalogEntry]:
        """The episodes and movies a selection names, each once, in the order of the library."""
        found: dict[int, CatalogEntry] = {}

        def add(entry: CatalogEntry) -> None:
            if entry.kind in (KIND_EPISODE, KIND_MOVIE) and entry.gone_at is None:
                found.setdefault(entry.id, entry)

        def add_series(series: CatalogEntry) -> None:
            for episode in self.store.catalog_entries(KIND_EPISODE, parent_id=series.id):
                if selection.season is None or episode.season == selection.season:
                    add(episode)

        if selection.everything:
            for entry in self.store.catalog_entries(KIND_EPISODE):
                add(entry)
            for entry in self.store.catalog_entries(KIND_MOVIE):
                add(entry)
        elif selection.series is not None:
            series = self.store.catalog_entry(selection.series)
            if series is None or series.kind != KIND_SERIES:
                raise LookupError(f"no series {selection.series} in the catalog")
            add_series(series)
        else:
            for item_id in selection.items:
                entry = self.store.catalog_entry(item_id)
                if entry is None:
                    raise LookupError(f"no item {item_id} in the catalog")
                if entry.kind == KIND_SERIES:
                    add_series(entry)
                else:
                    add(entry)
        return list(found.values())

    def target_codes(self, only: tuple[str, ...] = ()) -> list[str]:
        """The enabled target languages (all of them, or only the codes named)."""
        return [str(t["code"]) for t in enabled_target_languages(self._config())
                if not only or str(t["code"]).lower() in only]

    def label(self, entry: CatalogEntry, series: CatalogEntry | None) -> str:
        if entry.kind == KIND_EPISODE:
            code = f"S{(entry.season or 0):02d}E{(entry.episode or 0):02d}"
            return " · ".join(part for part in (series.title if series else "", code, entry.title) if part)
        return entry.title or entry.bazarr_id

    # -- planning --------------------------------------------------------------------------------------------------
    def plan(
        self, selection: Selection, mode: str = FILL, *, automatic: bool = False, entries: Iterable[CatalogEntry] | None = None,
        measure: bool = True,
    ) -> RequestPlan:
        """Decide, for each (item, language), whether a job would be made. ``automatic`` is the policy asking: it
        only takes languages Bazarr lists as missing and never what the admin removed (FR-15.4, FR-16.6).
        ``entries`` replaces the selection with items already chosen. ``measure`` reads each source subtitle to
        count its cues and calls (the estimate of a bulk request); the policy does not need that every few minutes."""
        if mode not in (FILL, REPLACE, AUTO_MODE):
            raise ValueError("mode must be 'fill', 'replace' or 'auto'")
        if automatic and mode != FILL:
            raise ValueError("automatic policy only supports 'fill' mode")
        config = self._config()
        sources, targets = enabled_source_languages(config), enabled_target_languages(config)
        if selection.languages:
            targets = [t for t in targets if str(t["code"]).lower() in selection.languages]
        chosen = list(entries) if entries is not None else self.leaves(selection)
        ignored = self.store.decisions()
        series_cache: dict[int, CatalogEntry | None] = {}
        dir_cache: dict[Path, list[Path]] = {}
        planned: list[PlannedJob] = []
        skipped: list[PlannedJob] = []
        for entry in chosen:
            if entry.parent_id not in series_cache:
                series_cache[entry.parent_id] = self.store.catalog_entry(entry.parent_id) if entry.parent_id else None
            title = self.label(entry, series_cache[entry.parent_id])
            unit = self.library.unit_of(entry)
            picked = pick_source(entry, sources)
            for target in targets:
                job = self._evaluate(
                    entry, title, unit.id if unit else None, picked, target, mode, ignored, automatic, measure,
                    dir_cache=dir_cache,
                )
                (skipped if job.skip else planned).append(job)
        return RequestPlan(tuple(planned), tuple(skipped), self.estimate(planned, extract=measure), mode)

    def _evaluate(
        self, entry: CatalogEntry, title: str, unit_id: int | None,
        picked: tuple[Any, dict[str, Any]] | None, target: dict[str, Any], mode: str, ignored: set[tuple[int, str]],
        automatic: bool, measure: bool, *, dir_cache: dict[Path, list[Path]] | None = None,
    ) -> PlannedJob:
        lang = str(target["code"])
        language = str(target["language"])

        def no(skip: str, **fields: Any) -> PlannedJob:
            return PlannedJob(entry.id, title, lang, language, skip=skip, **fields)

        if unit_id is not None and (unit_id, lang) in ignored:
            return no("ignored")
        if picked is None:
            return no("no-source")
        subtitle, source = picked
        source_code = str(source["code"])
        if source_code.lower() == lang.lower():
            return no("same-language")
        found = dict(source_path=subtitle.path, source_code=source_code, source_language=str(source["language"]))
        if not Path(subtitle.path).is_file():
            return no("source-missing", **found)
        output = target_output_path(subtitle.path, lang, source_code)
        found["output_path"] = output
        if outside_allowed_paths(subtitle.path):
            return no("outside-paths", **found)
        if automatic:
            if lang.lower() not in entry.missing:
                return no("not-missing", **found)
            if self.versions.latest_status(output) == "removed":
                return no("removed", **found)
            # What failed, or what the admin cancelled, waits for the admin: the policy never asks for it again (D4).
            ended = self.queue.jobs.last_ended_state(job_key(subtitle.path, output, lang), unit_id, lang)
            if ended in (FAILED, CANCELLED):
                return no(ended, **found)
        has_target = False
        if mode in (FILL, AUTO_MODE):
            listed = any(s.code == lang.lower() and not s.forced for s in entry.subtitles)
            has_target = listed or Path(output).exists() or bool(
                existing_target_subtitle(subtitle.path, lang, source_code, dir_cache=dir_cache)
            )
            if mode == FILL and has_target:
                return no("has-target", **found)
        effective_mode = REPLACE if (mode == REPLACE or (mode == AUTO_MODE and has_target)) else FILL
        if self._open_job(unit_id, subtitle.path, output, lang, effective_mode) is not None:
            return no("requested", **found)
        count = calls = 0
        if measure:
            try:
                cues = srt.parse_file(subtitle.path)
            except (OSError, srt.SrtError):
                return no("unreadable", **found)
            count, calls = len(cues), self._calls(cues)
        return PlannedJob(
            entry.id, title, lang, language,
            action="retranslate" if effective_mode == REPLACE else "translate",
            source_path=subtitle.path, output_path=output, source_code=source_code,
            source_language=str(source["language"]), cues=count, calls=calls,
        )

    def _open_job(self, unit_id: int | None, subtitle: str, output: str, lang: str, mode: str) -> dict[str, Any] | None:
        """An open job that already does what this request asks. A replace request over an open fill job is not
        "already requested": it turns that job into the replacement (FR-15.3)."""
        job = self.queue.jobs.open_for(job_key(subtitle, output, lang))
        if job is None and unit_id is not None:
            job = self.queue.jobs.open_for_unit(unit_id, lang)
        if job is None:
            return None
        if mode == REPLACE and job.get("mode") != REPLACE:
            return None
        return job

    def _calls(self, cues: list[srt.Cue]) -> int:
        """Model calls to translate these cues: the planner's parts (a repair or a split adds a few more)."""
        return self.estimator.calls_for(cues)

    def estimate(self, planned: list[PlannedJob], *, extract: bool = True) -> Estimate:
        return self.estimator.of(
            len(planned), sum(job.cues for job in planned), sum(job.calls for job in planned),
            self._extract_calls(planned) if extract else 0,
        )

    def _extract_calls(self, planned: list[PlannedJob]) -> int:
        """Story-fact reads the planned jobs would run first (FR-15.5.1): per series with memory on, each episode before
        a planned one in story order and the planned one itself that has a source and no facts yet, once for the whole
        request. The rule is ``StoryMemory.needs_extract``, the one ``ensure_facts`` follows; nothing is called."""
        if self.memory is None:
            return 0
        by_series: dict[int, list[CatalogEntry]] = {}
        for item_id in dict.fromkeys(job.item_id for job in planned):
            entry = self.store.catalog_entry(item_id)
            if entry is not None and entry.kind == KIND_EPISODE and entry.parent_id is not None:
                by_series.setdefault(entry.parent_id, []).append(entry)
        settings = self._settings()
        sources = enabled_source_languages(self._config())
        total = 0
        for parent_id, wanted in by_series.items():
            series = self.store.series_by_key(wanted[0].series_key)
            if not self.memory.series_enabled(series, settings):
                continue
            known = {(u.season, u.episode): u for u in self.store.list_units(series.id)} if series else {}
            units: list[Unit] = []
            chosen: dict[int, Unit] = {}
            for episode in self.store.catalog_entries(KIND_EPISODE, parent_id=parent_id):
                picked = pick_source(episode, sources)
                path = picked[0].path if picked else ""
                unit = known.pop((episode.season, episode.episode), None)
                if unit is None:  # the worker has not met it: it has no facts, as for a job that would register it now
                    unit = Unit(
                        id=-episode.id, series_id=series.id if series else None, season=episode.season,
                        episode=episode.episode, title=episode.title, media_type="series", source_path=path,
                    )
                elif path:
                    unit = dataclasses.replace(unit, source_path=path)
                units.append(unit)
                chosen[episode.id] = unit
            units.extend(known.values())
            total += len(self.memory.extract_needed(
                [chosen[e.id] for e in wanted if e.id in chosen], units, series, settings,
            ))
        return total

    # -- making the jobs -------------------------------------------------------------------------------------------
    def create(
        self, plan: RequestPlan, *, origin: str = "manual", prio: int = policy.INTERACTIVE, limit: int | None = None,
    ) -> Requested:
        """Make a job for each planned item (at most ``limit``; the rest stay as they were)."""
        created: list[str] = []
        merged = capped = 0
        skipped = list(plan.skipped)
        for n, job in enumerate(plan.planned):
            if limit is not None and len(created) + merged >= limit:
                capped = len(plan.planned) - n  # what the limit kept back, not what enqueue skipped for another reason
                break
            job_mode = REPLACE if job.action == "retranslate" else (FILL if job.action == "translate" else plan.mode)
            made = self._make(job, job_mode, origin, prio)
            if made is None:
                skipped.append(dataclasses.replace(job, skip="requested"))
            elif made[1]:
                merged += 1
            else:
                created.append(made[0])
        if created or merged:
            self.telemetry.event(
                "request.created", reason=f"{len(created)} job(s) were requested ({origin}), {merged} merged into jobs already open",
                origin=origin, mode=plan.mode, jobs=len(created), merged=merged,
            )
        return Requested(tuple(created), merged, tuple(skipped), capped)

    def _make(self, job: PlannedJob, mode: str, origin: str, prio: int) -> tuple[str, bool] | None:
        """``(job id, merged)``, or None when nothing was made. ``merged`` is a replace request that turned a job
        already open into the replacement instead of making a second one (FR-15.3)."""
        entry = self.store.catalog_entry(job.item_id)
        if entry is None:
            return None
        series = self.store.catalog_entry(entry.parent_id) if entry.parent_id else None
        unit_id = self.library.register_entry(
            entry, series, source_path=job.source_path, source_code=job.source_code, source_language=job.source_language,
        )
        self.store.set_output(unit_id, job.lang, job.output_path, job.language)
        base = {
            "provider": CONSOLE_PROVIDER, "video_path": entry.video_path, "subtitle_path": job.source_path,
            "output_path": job.output_path, "source_code": job.source_code, "source_language": job.source_language,
            "series_id": series.bazarr_id if series else "", "media_id": entry.bazarr_id,
            "media_type": "series" if entry.kind == KIND_EPISODE else "movie", "unit_id": unit_id, "catalog_id": entry.id,
        }
        if mode == REPLACE:
            open_before = self._open_job(unit_id, job.source_path, job.output_path, job.lang, FILL)
            try:
                job_id = self.queue.enqueue_retranslation({
                    **base, "target_code": job.lang, "target_language": job.language,
                    "retranslate": {"range": None, "correction_ids": None},
                })
            except ValueError as exc:
                logging.info("Not requesting a retranslation of %s: %s", job.title, exc)
                return None
            return job_id, open_before is not None
        made = self.queue.enqueue(base, [{"code": job.lang, "language": job.language, "enabled": True}],
                                  origin=origin, prio=prio)
        return (made[0], False) if made else None
