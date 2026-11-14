"""Story memory for the worker: facts per episode, prerequisites, gaps and the context bundle (P4, FR-5).

``subcore.memory`` knows how to extract, fold and render facts; this module decides *which* facts a unit
sees and when to wait for missing ones:

* story order is ``(season, episode)`` as Bazarr numbers them; season 0 (specials) sits outside the chain
  and sees the state at a configured point (FR-5.7);
* before a unit is translated, every earlier unit with a source subtitle has its facts (extracted on the
  spot when missing, cached by source sha256 + extraction prompt version);
* an earlier unit with no source subtitle is a *gap*: the job waits up to ``gap_wait`` and then runs with
  ``context_complete = false`` (FR-5.4);
* the *fingerprint* of the facts a translation used is stored with it; a different fingerprint now means
  the translation is stale, which is only ever flagged (FR-5.5, FR-5.6).

Facts of a unit are always "the newest stored facts for the current extraction prompt version", both when
a bundle is built and when staleness is checked, so the two can never disagree.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from subcore import srt
from subcore.memory import (
    DEFAULT_BUDGET_TOKENS,
    DEFAULT_RECENT_UNITS,
    EXTRACT_PROMPT_VERSION,
    Facts,
    FactsError,
    StoryContext,
    UnitFacts,
    build_context,
    compress_arc,
    entries_hash,
    extract_facts,
    fingerprint,
)
from subcore.providers import Provider

from .ports import CatalogRepo, MediaLibrary, MemoryRepo, Series, TranslationVersion, Unit
from .translation import ContextGapError
from .versions import RETRANSLATE, sha256_file

DEFAULT_GAP_WAIT_SECONDS = 1800  # FR-5.4: 30 minutes
GAP_RECHECK_SECONDS = 300  # while waiting, look again this often: the missing source may arrive any time
MEMORY_MODES = ("", "on", "off")

ProviderFor = Callable[[str], Provider]
_ANCHOR = re.compile(r"^\s*S(\d{1,3})E(\d{1,4})\s*$", re.IGNORECASE)


def unit_label(unit: Unit) -> str:
    return f"S{unit.season or 0:02d}E{unit.episode or 0:02d}"


def parse_anchor(text: str) -> tuple[int, int] | None:
    match = _ANCHOR.match(text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


@dataclass
class MemoryOptions:
    enabled: bool = False
    gap_wait_seconds: int = DEFAULT_GAP_WAIT_SECONDS
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    recent_units: int = DEFAULT_RECENT_UNITS
    model: str = ""

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "MemoryOptions":
        settings = settings or {}
        return cls(
            enabled=bool(settings.get("memory_enabled")),
            gap_wait_seconds=_int(settings.get("memory_gap_wait_seconds"), DEFAULT_GAP_WAIT_SECONDS),
            budget_tokens=_int(settings.get("memory_budget_tokens"), DEFAULT_BUDGET_TOKENS, 500),
            recent_units=_int(settings.get("memory_recent_episodes"), DEFAULT_RECENT_UNITS),
            model=str(settings.get("memory_model") or "").strip(),
        )


@dataclass
class MemoryPlan:
    """What a translation of one unit gets from the story memory."""

    sections: list[tuple[str, str]] = field(default_factory=list)
    fingerprint: str = ""
    complete: bool = True
    missing: list[str] = field(default_factory=list)  # earlier units with no usable facts
    compressed: str = ""


class StoryMemory:
    def __init__(
        self,
        facts: MemoryRepo,
        units: CatalogRepo,
        *,
        settings_source: Callable[[], dict[str, Any]] | None = None,
        media: MediaLibrary | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.facts = facts
        self.units = units
        self._settings_source = settings_source
        self.media = media
        self.clock = clock

    # -- policy ------------------------------------------------------------------
    def options(self, settings: dict[str, Any] | None = None) -> MemoryOptions:
        if settings is None and self._settings_source is not None:
            settings = self._settings_source()
        return MemoryOptions.from_settings(settings)

    def enabled_for(self, unit: Unit, settings: dict[str, Any] | None = None) -> bool:
        """Memory applies to episodes only; a series can follow the global switch or override it."""
        if unit.series_id is None:
            return False
        series = self.units.get_series(unit.series_id)
        policy = series.memory if series else ""
        if policy == "on":
            return True
        if policy == "off":
            return False
        return self.options(settings).enabled

    # -- story order ----------------------------------------------------------------
    @staticmethod
    def _chain(units: list[Unit]) -> list[Unit]:
        """The main storyline: seasons 1 and up in Bazarr order. Specials (season 0) are outside it."""
        return sorted(
            (u for u in units if (u.season or 0) >= 1 and u.episode is not None),
            key=lambda u: (u.season, u.episode, u.id),
        )

    def prior_units(self, unit: Unit, units: list[Unit], series: Series | None) -> list[Unit]:
        """Units whose facts form the story state at the start of ``unit`` (never a later unit)."""
        chain = self._chain(units)
        if (unit.season or 0) >= 1:
            here = (unit.season, unit.episode)
            return [u for u in chain if (u.season, u.episode) < here and u.id != unit.id]
        # A special: the state at the configured point, by default after the first season (FR-5.7).
        anchor = parse_anchor(series.specials_after if series else "")
        if anchor is None:
            if not chain:
                return []
            first = chain[0].season
            return [u for u in chain if u.season == first]
        return [u for u in chain if (u.season, u.episode) <= anchor]

    @staticmethod
    def has_source(unit: Unit) -> bool:
        return bool(unit.source_path) and Path(unit.source_path).is_file()

    # -- facts --------------------------------------------------------------------
    def _stored(self, units: list[Unit]) -> dict[int, Facts]:
        rows = self.facts.latest_facts([u.id for u in units], EXTRACT_PROMPT_VERSION)
        return {uid: row.facts for uid, row in rows.items()}

    def ensure_facts(
        self, unit: Unit, title: str, settings: dict[str, Any], provider_for: ProviderFor | None
    ) -> tuple[Facts | None, str]:
        """Facts for ``unit``: from the cache, or extracted now. Returns ``(facts, how)``.

        ``how`` is ``cached``, ``extracted``, ``stored`` (source gone or unreadable; older facts kept),
        ``no-source`` or ``failed``. Provider errors (quota, outage) propagate to the queue.
        """
        unit_id = unit.id
        latest = self.facts.latest_facts([unit_id], EXTRACT_PROMPT_VERSION).get(unit_id)
        kept = latest.facts if latest else None
        if not self.has_source(unit):
            return (kept, "stored") if kept is not None else (None, "no-source")
        path = Path(unit.source_path)
        try:
            sha = sha256_file(path)
        except OSError:
            return (kept, "stored") if kept is not None else (None, "failed")
        row = self.facts.get_facts(unit_id, sha, EXTRACT_PROMPT_VERSION, touch=True)
        if row is not None:
            return row.facts, "cached"
        if provider_for is None:
            return (kept, "stored") if kept is not None else (None, "failed")
        try:
            cues = srt.parse_file(path)
            if not cues:
                raise FactsError("the source subtitle has no cues")
            result = extract_facts(
                provider_for("extract"), cues, title=title, season=unit.season, episode=unit.episode,
                source_lang=unit.source_language or unit.source_code or None,
            )
        except (FactsError, srt.SrtError, OSError) as exc:
            logging.warning("Could not extract story facts for %s %s: %s", title or "unit", unit_label(unit), exc)
            return (kept, "stored") if kept is not None else (None, "failed")
        self.facts.save_facts(
            unit_id, sha, EXTRACT_PROMPT_VERSION, model=result.model, facts=result.facts,
            tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        )
        logging.info("Extracted story facts for %s %s (%s tokens in)", title or "unit", unit_label(unit), result.tokens_in)
        return result.facts, "extracted"

    # -- the series catalogue (FR-8.3) --------------------------------------------------
    def refresh_catalog(self, unit: Unit, series: Series, source_code: str) -> None:
        """Learn the episodes Bazarr lists for the series, with their source subtitles (best effort)."""
        if self.media is None or not series.bazarr_id:
            return
        try:
            episodes = self.media.series_episodes(series.bazarr_id, source_code)
        except Exception as exc:  # noqa: BLE001 - the catalogue only improves gap detection
            logging.warning("Could not list the episodes of %s from Bazarr (%s)", series.title, type(exc).__name__)
            return
        for item in episodes:
            if (item.season, item.episode) == (unit.season, unit.episode):
                continue
            self.units.upsert_unit(
                series_id=series.id, season=item.season, episode=item.episode, title=item.title,
                media_type="series", bazarr_id=item.bazarr_id, video_path=item.video_path,
                source_path=item.source_path, source_code=source_code if item.source_path else "",
            )

    # -- the bundle for one translation --------------------------------------------------
    def _compressor(self, series_id: int, up_to_unit: int, provider_for: ProviderFor | None):
        def compress(entries: list[tuple[str, str]]) -> str:
            key = entries_hash(entries)
            cached = self.facts.get_summary(key)
            if cached is not None:
                return cached
            if provider_for is None:
                return ""  # a preview never spends a call
            text = compress_arc(provider_for("summarize"), entries)
            self.facts.put_summary(series_id, up_to_unit, key, text)
            return text

        return compress

    def _context(
        self, prior: list[tuple[Unit, Facts]], own: Facts | None, series_id: int, unit_id: int,
        options: MemoryOptions, provider_for: ProviderFor | None,
    ) -> StoryContext:
        return build_context(
            [UnitFacts(unit_label(u), f) for u, f in prior], own,
            budget_tokens=options.budget_tokens, recent=options.recent_units,
            compress=self._compressor(series_id, unit_id, provider_for),
        )

    def plan(
        self,
        unit: Unit,
        job: dict[str, Any],
        settings: dict[str, Any],
        provider_for: ProviderFor,
    ) -> MemoryPlan:
        """Make sure every earlier unit has facts, wait out gaps, and build the context for ``unit``.

        Raises :class:`ContextGapError` while an earlier unit has no source subtitle and ``gap_wait``
        has not passed (never for an explicit retranslation, which runs with what exists).
        """
        options = self.options(settings)
        series = self.units.get_series(unit.series_id)
        title = series.title
        self.refresh_catalog(unit, series, str(job.get("source_code") or unit.source_code or ""))
        units = self.units.list_units(series.id)

        prior: list[tuple[Unit, Facts]] = []
        missing: list[str] = []
        gap: list[str] = []
        for earlier in self.prior_units(unit, units, series):
            facts, how = self.ensure_facts(earlier, title, settings, provider_for)
            if facts is None:
                missing.append(unit_label(earlier))
                if how == "no-source":
                    gap.append(unit_label(earlier))
            else:
                prior.append((earlier, facts))

        if gap and job.get("trigger") != RETRANSLATE and options.gap_wait_seconds > 0:
            now = self.clock()
            since = float(job.get("gap_since") or now)
            if now - since < options.gap_wait_seconds:
                retry_at = now + min(GAP_RECHECK_SECONDS, since + options.gap_wait_seconds - now)
                raise ContextGapError(
                    f"Waiting for the source subtitle of {', '.join(gap)} before translating {unit_label(unit)}",
                    retry_at=retry_at, since=since, missing=gap,
                )

        own, _ = self.ensure_facts(unit, title, settings, provider_for)
        context = self._context(prior, own, series.id, unit.id, options, provider_for)
        self.facts.put_snapshot(context.fingerprint, context.sections)
        return MemoryPlan(context.sections, context.fingerprint, not missing, missing, context.compressed)

    # -- staleness (FR-5.5) ------------------------------------------------------------------
    def _series_view(self, series_id: int, cache: dict | None) -> tuple[list[Unit], dict[int, Facts]]:
        slot = ("memory", series_id)
        if cache is not None and slot in cache:
            return cache[slot]
        units = self.units.list_units(series_id)
        view = (units, self._stored(units))
        if cache is not None:
            cache[slot] = view
        return view

    def fingerprint(self, unit: Unit, cache: dict | None = None) -> str:
        """The fingerprint a translation of ``unit`` made right now would carry (no model call)."""
        series = self.units.get_series(unit.series_id)
        units, facts = self._series_view(series.id, cache)
        prior = [UnitFacts(unit_label(u), facts[u.id]) for u in self.prior_units(unit, units, series) if u.id in facts]
        return fingerprint(prior, facts.get(unit.id))

    def snapshot(self, memory_hash: str) -> list[tuple[str, str]] | None:
        """The context sections a fingerprint stood for, if they were kept."""
        return self.facts.get_snapshot(memory_hash)

    def is_stale(self, unit: Unit, version: TranslationVersion, cache: dict | None = None) -> bool:
        """Was ``version`` made with different story memory than ``unit`` has now? (flag only)"""
        used = version.memory_hash
        if not used or not self.enabled_for(unit):
            return False  # made without memory, or memory is off for this series
        if self.fingerprint(unit, cache) != used:
            return True
        # A unit that was missing when this was translated has a source subtitle now.
        wanted = version.missing_units
        if not wanted:
            return False
        units, _ = self._series_view(unit.series_id, cache)
        by_label = {unit_label(u): u for u in units}
        return any(label in by_label and self.has_source(by_label[label]) for label in wanted)

    # -- for the Context tab -------------------------------------------------------------------
    def coverage(self, series_id: int, cache: dict | None = None) -> list[dict[str, Any]]:
        units, facts = self._series_view(series_id, cache)
        return [
            {
                "id": u.id, "label": unit_label(u), "season": u.season, "episode": u.episode,
                "has_source": self.has_source(u), "has_facts": u.id in facts,
                "facts_hash": facts[u.id].hash[:12] if u.id in facts else "",
            }
            for u in sorted(units, key=lambda u: (u.season or 0, u.episode or 0, u.id))
        ]

    def preview(self, unit: Unit, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        """The bundle ``unit`` would get from the facts stored now. Reads only: never extracts or compresses."""
        series = self.units.get_series(unit.series_id)
        options = self.options(settings)
        units = self.units.list_units(series.id)
        facts = self._stored(units)
        prior_units = self.prior_units(unit, units, series)
        prior = [(u, facts[u.id]) for u in prior_units if u.id in facts]
        own = facts.get(unit.id)
        context = self._context(prior, own, series.id, unit.id, options, None)
        return {
            "fingerprint": context.fingerprint,
            "sections": [{"title": t, "body": b} for t, b in context.sections],
            "approximate": context.compressed == "truncated",
            "prior": [
                {"label": unit_label(u), "has_source": self.has_source(u), "has_facts": u.id in facts}
                for u in prior_units
            ],
            "missing": [unit_label(u) for u in prior_units if u.id not in facts],
            "own_facts": own.to_dict() if own else None,
            "own_has_source": self.has_source(unit),
        }

    def refresh(self, unit: Unit, settings: dict[str, Any], provider_for: ProviderFor) -> dict[str, Any]:
        """Extract the facts of ``unit`` and of every earlier unit that has a source (spends model calls)."""
        series = self.units.get_series(unit.series_id)
        title = series.title
        self.refresh_catalog(unit, series, unit.source_code)
        units = self.units.list_units(series.id)
        results: dict[str, str] = {}
        for target in [*self.prior_units(unit, units, series), self.units.get_unit(unit.id) or unit]:
            _, how = self.ensure_facts(target, title, settings, provider_for)
            results[unit_label(target)] = how
        return {"extracted": [k for k, v in results.items() if v == "extracted"], "results": results}
