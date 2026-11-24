"""Actions on a library unit: view cues, correct, retranslate, inspect and revert versions (FR-10, FR-11).

``UnitService`` is what the HTTP layer calls (like ``ConsoleActions`` for the queue); it owns no
HTTP details. ``LookupError`` means "not found" and ``ValueError`` means "bad request".

It holds the lookups every action shares (``unit``, ``output``, the source and target cues, the Bazarr rescan)
and the cue page; the work is in five parts, each a mixin of its own file: ``unit_corrections.py``,
``unit_requests.py``, ``unit_versions.py``, ``unit_context.py`` and ``unit_glossary.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from subcore import srt

from .estimates import Estimator
from .glossaries import Glossaries
from .library import Library
from ..domain.ports import Output, Telemetry, Unit
from ..adapters.store import Store
from ..jobs.queue import JobQueue
from .memory import StoryMemory
from ..domain.rules import format_ms, parse_time_ms
from ..adapters.telemetry import Telemetry as TelemetrySink
from .versions import TranslationVersions
from .unit_context import ContextMixin
from .unit_corrections import CorrectionsMixin
from .unit_glossary import GlossaryMixin
from .unit_requests import RequestsMixin
from .unit_versions import VersionsMixin


MAX_CUE_PAGE = 5000


class UnitService(CorrectionsMixin, RequestsMixin, VersionsMixin, ContextMixin, GlossaryMixin):
    feedback: Any = None  # ``FeedbackProposer``: told after cue edits, wired by the composition root (P15)
    judge: Any = None  # ``JudgeService``: its verdicts are laid over a comparison and it queues the judging (P15)

    def __init__(
        self,
        library: Library,
        glossaries: Glossaries,
        versions: TranslationVersions,
        jobs: JobQueue,
        refresh_bazarr: Callable[[dict[str, Any]], None] | None = None,
        memory: StoryMemory | None = None,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        usage: Store | None = None,
        telemetry: Telemetry | None = None,
        estimator: Estimator | None = None,
        source_languages: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.telemetry: Telemetry = telemetry or TelemetrySink(usage=usage, synchronous=True)
        self._source_languages = source_languages  # the enabled source languages, to pick a source subtitle from the catalog
        self.library = library
        self.catalog = library.catalog
        self.version_repo = versions.repo
        self.usage = usage
        self.glossaries = glossaries
        self.versions = versions
        self.jobs = jobs
        self._refresh = refresh_bazarr
        self.memory = memory
        self._settings = settings_source or (lambda: {})
        self.estimator = estimator or Estimator(jobs.jobs.store, jobs, self._settings)

    # -- lookups -----------------------------------------------------------------
    def unit(self, unit_id: int) -> Unit:
        unit = self.catalog.get_unit(unit_id)
        if unit is None:
            raise LookupError(f"no unit {unit_id}")
        return unit

    def output(self, unit: Unit, lang: str | None) -> Output:
        outputs = self.catalog.outputs(unit.id)
        if lang:
            for out in outputs:
                if out.target_lang == lang:
                    return out
            raise LookupError(f"this unit has no '{lang}' target")
        if len(outputs) == 1:
            return outputs[0]
        raise ValueError("lang is required: this unit has " + ("no targets" if not outputs else "several targets"))

    def _source(self, unit: Unit) -> list[srt.Cue]:
        return self.library.source_cues(unit)

    def _target(self, out: Output) -> list[srt.Cue]:
        try:
            return srt.parse_file(out.output_path)
        except OSError:
            raise LookupError("the target subtitle is not on disk") from None
        except srt.SrtError as exc:
            raise ValueError(f"the target subtitle cannot be read: {exc}") from None

    REFRESH_ATTEMPTS = 3
    REFRESH_DELAY_SECONDS = 5.0

    def _bazarr_refresh(self, unit: Unit) -> None:
        """Ask Bazarr to rescan the unit in the background (FR-16 `refresh`): the file is already in place, so a
        slow or failing Bazarr neither delays the console nor changes any job's result."""
        if self._refresh is None:
            return
        series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        payload = {
            "media_type": unit.media_type or ("series" if series else "movie"),
            "series_id": series.bazarr_id if series else "",
            "media_id": unit.bazarr_id,
        }
        threading.Thread(target=self._refresh_with_retry, args=(payload,), name="bazarr-refresh", daemon=True).start()

    def _refresh_with_retry(self, payload: dict[str, Any]) -> bool:
        for attempt in range(1, self.REFRESH_ATTEMPTS + 1):
            try:
                self._refresh(payload)  # type: ignore[misc] - checked by _bazarr_refresh
                return True
            except Exception as exc:  # noqa: BLE001 - Bazarr rescans on its own schedule if this never lands
                logging.warning(
                    "Bazarr scan after a subtitle change failed (%s), attempt %d of %d",
                    type(exc).__name__, attempt, self.REFRESH_ATTEMPTS,
                )
                if attempt < self.REFRESH_ATTEMPTS:
                    time.sleep(self.REFRESH_DELAY_SECONDS * attempt)
        return False

    # -- detail ----------------------------------------------------------------------
    def detail(self, unit_id: int) -> dict[str, Any]:
        unit = self.unit(unit_id)
        summary = self.library.unit_summary(unit)
        series = self.catalog.get_series(unit.series_id) if unit.series_id is not None else None
        return {
            **summary,
            "series": {"id": series.id, "title": series.title} if series else None,
            "source_exists": bool(unit.source_path) and Path(unit.source_path).is_file(),
            "memory_applicable": series is not None and self.memory is not None,
        }

    def cues(
        self,
        unit_id: int,
        lang: str | None = None,
        *,
        at: str | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = MAX_CUE_PAGE,
    ) -> dict[str, Any]:
        """Source and translation side by side; ``at`` jumps to a player time, ``q`` searches both texts."""
        unit = self.unit(unit_id)
        out = self.output(unit, lang)
        source = self._source(unit)
        try:
            target = self._target(out)
        except LookupError:
            target = []
        locked = {
            int(c.payload["cue"]) for c in self.catalog.corrections(unit_id, out.target_lang) if c.kind == "cue_edit"
        }
        rows = [
            {
                "n": n, "index": cue.index, "start": format_ms(cue.start_ms), "start_ms": cue.start_ms,
                "end_ms": cue.end_ms, "source": cue.text,
                "target": target[n - 1].text if n <= len(target) else None,
                "locked": n in locked,
            }
            for n, cue in enumerate(source, start=1)
        ]
        focus = None
        if at:
            ms = parse_time_ms(at)
            inside = [r for r in rows if r["start_ms"] <= ms <= r["end_ms"]]
            nearest = inside[0] if inside else min(rows, key=lambda r: abs(r["start_ms"] - ms), default=None)
            focus = nearest["n"] if nearest else None
        if q and q.strip():
            needle = q.strip().casefold()
            rows = [r for r in rows if needle in r["source"].casefold() or needle in (r["target"] or "").casefold()]
        total = len(rows)
        limit = max(1, min(int(limit), MAX_CUE_PAGE))
        if focus is not None and not (q and q.strip()):
            offset = max(0, focus - 1 - limit // 2)
        offset = max(0, int(offset))
        return {
            "lang": out.target_lang, "total": total, "offset": offset, "focus": focus,
            "aligned": len(target) == len(source), "has_target": bool(target),
            "rows": rows[offset : offset + limit],
        }
