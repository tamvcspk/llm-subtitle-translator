"""The story memory of an episode and of its series, as the Context tab shows and changes it (FR-5, FR-15.11).

``ContextMixin`` is a part of ``UnitService`` (``units.py``).
"""

from __future__ import annotations

from typing import Any

from .catalog import pick_source
from ..domain.config import model_for_task
from ..domain.ports import KIND_EPISODE, KIND_SERIES, Output, Unit
from .memory import MEMORY_MODES, parse_anchor, unit_label


class ContextMixin:
    def _target_or_none(self, unit: Unit, lang: str | None) -> Output | None:
        try:
            return self.output(unit, lang)
        except (LookupError, ValueError):
            return None

    def context(self, unit_id: int, lang: str | None = None) -> dict[str, Any]:
        """What the story memory gave this translation, and what it would give now (FR-5.2, ADR-7 Context tab)."""
        unit = self.unit(unit_id)
        if unit.series_id is None or self.memory is None:
            return {"applicable": False, "enabled": False}
        series = self.catalog.get_series(unit.series_id)
        out = self._target_or_none(unit, lang)
        current = self.version_repo.current_version(out.output_path) if out else None
        now = self.memory.preview(unit, self._settings())
        used = None
        if current and current.memory_hash:
            sections = self.memory.snapshot(current.memory_hash)
            used = {
                "version": current.version,
                "complete": current.context_complete,
                "missing": list(current.missing_units),
                "sections": [{"title": t, "body": b} for t, b in sections] if sections is not None else None,
                "changed": current.memory_hash != now["fingerprint"],
            }
        return {
            "applicable": True,
            "enabled": self.memory.enabled_for(unit, self._settings()),
            "policy": series.memory,
            "specials_after": series.specials_after,
            "label": unit_label(unit),
            "special": (unit.season or 0) == 0,
            "lang": out.target_lang if out else None,
            "used": used,
            "now": now,
        }

    def adopt_catalog_episodes(self, series_id: int) -> int:
        """Make a unit for every episode of the series the catalog lists, with the source subtitle it picks, so the
        episodes of a series whose subtitles were translated before (or by someone else) still show in Context and can
        have their facts read. A unit is otherwise made only when the admin acts on an item or a job runs. Idempotent;
        returns how many units it made or gave a source."""
        if self._source_languages is None:
            return 0
        series = self.catalog.get_series(series_id)
        entry = self.catalog.catalog_lookup(KIND_SERIES, series.bazarr_id) if series and series.bazarr_id else None
        if entry is None:
            return 0
        sources = self._source_languages()
        changed = 0
        for episode in self.catalog.catalog_entries(KIND_EPISODE, parent_id=entry.id):
            if episode.season is None or episode.episode is None:
                continue
            unit = self.library.unit_of(episode)
            picked = pick_source(episode, sources)
            if unit is not None and (unit.source_path or picked is None):
                continue
            path, code, language = (picked[0].path, str(picked[1]["code"]), str(picked[1]["language"])) if picked else ("", "", "")
            self.library.register_entry(episode, entry, source_path=path, source_code=code, source_language=language)
            changed += 1
        return changed

    def adopt_episodes(self, series_id: int) -> dict[str, Any]:
        """``POST /api/series/{id}/memory/adopt``: the admin opened the Context tab of this series, so its episodes get
        the units that tab lists (see ``adopt_catalog_episodes``). Idempotent; ``adopted`` is how many it made."""
        if self.catalog.get_series(series_id) is None or self.memory is None:
            raise LookupError(f"no series {series_id}")
        return {"ok": True, "adopted": self.adopt_catalog_episodes(series_id)}

    def series_memory(self, series_id: int) -> dict[str, Any]:
        series = self.catalog.get_series(series_id)
        if series is None or self.memory is None:
            raise LookupError(f"no series {series_id}")
        settings = self._settings()
        # A read: it makes no unit. The Context tab asks for the units of the catalog's episodes with ``adopt_episodes``
        # when it opens (a POST), so a poll of this answer never writes (R14-3).
        return {
            "series": {"id": series.id, "title": series.title},
            "policy": series.memory,
            "specials_after": series.specials_after,
            "global_enabled": self.memory.options(settings).enabled,
            "extract_model": model_for_task(settings, "extract"),  # what "Extract facts" reads on (the Estimate box names it)
            "units": self.memory.coverage(series_id),
        }

    def set_series_memory(self, series_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Switch memory on or off for one series, or let it follow the global setting; anchor its specials."""
        series = self.catalog.get_series(series_id)
        if series is None or self.memory is None:
            raise LookupError(f"no series {series_id}")
        policy = body.get("policy")
        anchor = body.get("specials_after")
        if policy is not None and policy not in MEMORY_MODES:
            raise ValueError("policy must be '' (follow the global setting), 'on' or 'off'")
        if anchor is not None:
            anchor = str(anchor).strip().upper()
            if anchor and parse_anchor(anchor) is None:
                raise ValueError("specials_after must look like S02E05")
        self.catalog.set_series_memory(series_id, memory=policy, specials_after=anchor)
        return self.series_memory(series_id)

    def unit_facts(self, unit_id: int) -> dict[str, Any]:
        """What the facts stored for this episode say (reads only, no model call), for the Context tab to show."""
        unit = self.unit(unit_id)
        if unit.series_id is None or self.memory is None:
            raise ValueError("story memory applies to episodes of a series only")
        return self.memory.own_facts(unit)

    def refresh_context(self, unit_id: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read the facts of this episode and of the earlier ones that have a source subtitle (spends calls): one
        ``extract`` job per episode, in story order, that the queue runs under the same quota and breaker as a
        translation. The answer is the jobs made; the episodes left out say why (``no-source``: nothing to read;
        ``cached``: the newest facts are those of the source as it is now).

        ``unit_ids`` names exactly the episodes to read instead (of this series; nothing else is read)."""
        body = body or {}
        unit = self.unit(unit_id)
        if unit.series_id is None or self.memory is None:
            raise ValueError("story memory applies to episodes of a series only")
        only = self._picked_units(unit, body.get("unit_ids"))
        series = self.catalog.get_series(unit.series_id)
        wanted: list[dict[str, Any]] = []
        skipped: dict[str, str] = {}
        for target in self.memory.targets(unit, only):
            why = self.memory.needs_reading(target)
            if why:
                skipped[unit_label(target)] = why
                continue
            wanted.append({
                "unit_id": target.id, "label": unit_label(target), "title": series.title if series else target.title,
                # What fairness between series goes by: the same id the series' translation jobs carry.
                "series_id": series.bazarr_id if series else "",
            })
        created, merged = self.jobs.enqueue_extract(wanted)
        return {"ok": True, "job_ids": created, "queued": len(created), "merged": len(merged), "skipped": skipped}

    def _picked_units(self, unit: Unit, raw: Any) -> list[int] | None:
        """The episodes the admin ticked: whole numbers, all of this series. ``None`` when nothing was picked."""
        if raw is None:
            return None
        if not isinstance(raw, list) or not raw:
            raise ValueError("unit_ids must be a list of episode ids")
        ids: list[int] = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("unit_ids must be a list of episode ids")
            ids.append(value)
        own = {u.id for u in self.catalog.list_units(unit.series_id)}
        stray = [i for i in ids if i not in own]
        if stray:
            raise ValueError(f"episode {stray[0]} does not belong to this series")
        return list(dict.fromkeys(ids))
