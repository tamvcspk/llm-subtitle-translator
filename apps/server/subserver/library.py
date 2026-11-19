"""The library: series, episodes and movies the worker has met, and where each translation stands (FR-10).

A unit (one episode or one movie) is registered when a job for it runs. Its identity comes from the
job (Bazarr ids) and, failing that, from the file name; Bazarr is only asked for the title, season and
episode when the worker has the API key (`enrich_job_identity`).

A translation is ``stale`` when it was made with different rules than the ones that apply now (glossary
file or episode corrections changed; FR-5.5). Staleness is only ever flagged, never acted on (FR-5.6).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Callable

from subcore import glossary as g
from subcore import srt
from subcore.style import Style, StyleError

from .glossaries import MOVIES, SERIES, Glossaries
from .ports import KIND_EPISODE, KIND_MOVIE, CatalogEntry, MediaLibrary, Output, Unit
from .store import Store
from .rules import StyleLayers, UnitRules, build_rules
from .styles import Styles
from .versions import TranslationVersions

STATUSES = ("current", "stale", "incomplete", "untranslated")
_ATTENTION = {"stale": 0, "incomplete": 1, "untranslated": 2, "current": 3}

_EPISODE = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})")
_X_EPISODE = re.compile(r"(?<![\dA-Za-z])(\d{1,2})x(\d{2,3})(?!\d)")
_SEASON_DIR = re.compile(r"^(season|series|staffel|saison|temporada)\s*\d+$|^s\d{1,2}$", re.IGNORECASE)


@dataclass(frozen=True)
class Owner:
    """What owns a glossary, a style and character cards: a series (all its episodes) or one movie (its single unit).
    ``id`` is the series id, or the movie's unit id; ``units`` are the translations a change would reach."""
    kind: str  # SERIES | MOVIES, the ``owner_kind`` of the stored rows
    key: str
    id: int
    title: str
    units: tuple[Unit, ...]

    @property
    def label(self) -> str:
        return "series" if self.kind == SERIES else "movie"


@dataclass
class Identity:
    series_key: str | None = None
    series_title: str = ""
    movie_key: str | None = None
    season: int | None = None
    episode: int | None = None
    title: str = ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[._]+", " ", text)).strip(" -.")


def _stem(path: str) -> str:
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def series_key_of(tvdb_id: Any, series_id: Any, title: str = "") -> str:
    """The key of a series: its TVDB id, else Bazarr's id, else the title (the last resort of a file name)."""
    if tvdb_id:
        return f"tvdb-{tvdb_id}"
    if series_id:
        return f"bazarr-{series_id}"
    return f"title-{_slug(title) or 'unknown'}"


def movie_key_of(video_path: str, media_id: Any, subtitle_path: str = "") -> str:
    """The key of a movie: its TMDB id when the folder carries one, else Bazarr's id, else a hash of the path."""
    tmdb = re.search(r"\{tmdb-(\d+)\}", video_path)
    if tmdb:
        return f"tmdb-{tmdb.group(1)}"
    if media_id:
        return f"bazarr-movie-{media_id}"
    return "path-" + hashlib.sha1((video_path or subtitle_path).encode("utf-8")).hexdigest()[:12]


def identify_job(job: dict[str, Any]) -> Identity:
    """Work out which series/episode (or movie) a job is, from Bazarr ids, then from the file name."""
    video = str(job.get("video_path") or "")
    subtitle = str(job.get("subtitle_path") or "")
    stem = _stem(video or subtitle)
    media_type = str(job.get("media_type") or "")

    season = episode = None
    if job.get("season") not in (None, "") and job.get("episode") not in (None, ""):
        season, episode = int(job["season"]), int(job["episode"])
    else:
        match = _EPISODE.search(stem) or _X_EPISODE.search(stem)
        if match:
            season, episode = int(match.group(1)), int(match.group(2))

    if media_type != "movie" and season is not None and episode is not None:
        parts = [p for p in re.split(r"[\\/]", video or subtitle) if p][:-1]
        folder = ""
        if parts:
            folder = parts[-2] if len(parts) > 1 and _SEASON_DIR.match(parts[-1]) else parts[-1]
        match = _EPISODE.search(stem) or _X_EPISODE.search(stem)
        prefix = _clean_title(stem[: match.start()]) if match else ""
        title = str(job.get("series_title") or "").strip() or (prefix if len(prefix) >= 2 else _clean_title(folder))
        key = series_key_of(job.get("tvdb_id"), job.get("series_id"), title)
        return Identity(series_key=key, series_title=title or key, season=season, episode=episode,
                        title=str(job.get("episode_title") or "").strip())

    title = str(job.get("movie_title") or "").strip() or _clean_title(stem)
    key = movie_key_of(video, job.get("media_id") if media_type == "movie" else "", subtitle)
    return Identity(movie_key=key, title=title or key)


def enrich_job_identity(job: dict[str, Any], media: MediaLibrary) -> None:
    """Add the media library's title/season/episode to ``job`` (best effort; the file name is the fallback)."""
    if not job.get("media_id"):
        return
    try:
        details = media.describe(
            str(job.get("media_type") or ""), str(job.get("series_id") or ""), str(job["media_id"])
        )
    except Exception as exc:  # noqa: BLE001 - identity is a convenience; never fail a job over it
        logging.warning("Could not read media details from Bazarr (%s); using the file name", type(exc).__name__)
        return
    if details is None:
        return
    if details.series_title:
        job["series_title"] = details.series_title
    if details.tvdb_id:
        job["tvdb_id"] = details.tvdb_id
    if details.season is not None and details.episode is not None:
        job["season"], job["episode"] = details.season, details.episode
    if details.episode_title:
        job["episode_title"] = details.episode_title
    if details.movie_title:
        job["movie_title"] = details.movie_title


class Library:
    def __init__(
        self, catalog: Store, glossaries: Glossaries, versions: TranslationVersions, memory: Any = None,
        styles: Styles | None = None,
    ) -> None:
        self.catalog = catalog
        self.glossaries = glossaries
        self.versions = versions
        self.memory = memory  # StoryMemory | None: when set, story memory also decides staleness (FR-5.5)
        self.styles = styles  # Styles | None: without it an episode has no style layers (FR-18)

    # -- registering ---------------------------------------------------------
    def register_job(self, job: dict[str, Any]) -> int:
        """Make sure the job's unit and target file are known; returns the unit id."""
        if job.get("unit_id"):
            unit_id = int(job["unit_id"])
        else:
            ident = identify_job(job)
            common = dict(
                title=ident.title,
                media_type=str(job.get("media_type") or ""),
                bazarr_id=str(job.get("media_id") or ""),
                video_path=str(job.get("video_path") or ""),
                source_path=str(job.get("subtitle_path") or ""),
                source_code=str(job.get("source_code") or ""),
                source_language=str(job.get("source_language") or ""),
            )
            if ident.series_key:
                series_id = self.catalog.upsert_series(
                    ident.series_key, title=ident.series_title, bazarr_id=str(job.get("series_id") or "")
                )
                unit_id = self.catalog.upsert_unit(
                    series_id=series_id, season=ident.season, episode=ident.episode, **common
                )
            else:
                unit_id = self.catalog.upsert_unit(movie_key=ident.movie_key, **common)
        output = str(job.get("output_path") or "")
        if output and job.get("target_code"):
            self.catalog.set_output(unit_id, str(job["target_code"]), output, str(job.get("target_language") or ""))
        return unit_id

    def unit_of(self, entry: CatalogEntry) -> Unit | None:
        """The unit the worker keeps for a catalog item, once one exists (it is made when the admin first acts on
        the item or a job runs); None before that."""
        if entry.kind == KIND_EPISODE:
            series = self.catalog.series_by_key(entry.series_key)
            if series is None or entry.season is None or entry.episode is None:
                return None
            return self.catalog.find_unit(series.id, entry.season, entry.episode)
        if entry.kind == KIND_MOVIE:
            return self.catalog.unit_by_movie_key(entry.series_key)
        return None

    def register_entry(
        self, entry: CatalogEntry, series: CatalogEntry | None, *, source_path: str = "", source_code: str = "",
        source_language: str = "",
    ) -> int:
        """Make sure the unit of a catalog item exists and knows where its files are; returns the unit id."""
        common = dict(
            title=entry.title, bazarr_id=entry.bazarr_id, video_path=entry.video_path, source_path=source_path,
            source_code=source_code, source_language=source_language,
        )
        if entry.kind == KIND_EPISODE:
            series_id = self.catalog.upsert_series(
                entry.series_key, title=series.title if series else "", bazarr_id=series.bazarr_id if series else "",
            )
            return self.catalog.upsert_unit(
                series_id=series_id, season=entry.season, episode=entry.episode, media_type="series", **common
            )
        return self.catalog.upsert_unit(movie_key=entry.series_key, media_type="movie", **common)

    # -- rules ---------------------------------------------------------------
    def owner(self, unit: Unit) -> tuple[str, str]:
        if unit.series_id is not None:
            series = self.catalog.get_series(unit.series_id)
            return SERIES, series.key
        return MOVIES, str(unit.movie_key)

    def unit_rules(
        self,
        unit: Unit,
        lang: str,
        *,
        cues: list[srt.Cue] | None = None,
        correction_ids: list[int] | None = None,
        glossary: g.Glossary | None = None,
        cache: dict | None = None,
        styles: StyleLayers | None = None,
    ) -> UnitRules:
        """Rules for one unit and language; ``correction_ids`` narrows the episode corrections used. ``styles``
        replaces the stored style layers (a variant, or the preview of a style that is not saved yet)."""
        kind, key = self.owner(unit)
        if glossary is None:
            slot = (kind, key, lang)
            if cache is not None and slot in cache:
                glossary = cache[slot]
            else:
                glossary = self.glossaries.load(kind, key, lang)
                if cache is not None:
                    cache[slot] = glossary
        corrections = self.catalog.corrections(unit.id, lang)
        if correction_ids is not None:
            wanted = set(correction_ids)
            corrections = [c for c in corrections if c.kind == "cue_edit" or c.id in wanted]
        return build_rules(
            glossary, season=unit.season, episode=unit.episode, corrections=corrections, cues=cues, lang=lang,
            styles=styles if styles is not None else self.style_layers(unit, cache),
        )

    def style_layers(self, unit: Unit, cache: dict | None = None) -> StyleLayers:
        """The stored style layers that apply to a unit: its series' (or movie's) preset, style and cards, and its own."""
        if self.styles is None:
            return StyleLayers()
        kind, key = self.owner(unit)
        slot = ("style", kind, key)
        if cache is not None and slot in cache:
            base = cache[slot]
        else:
            base = self.styles.layers(kind, key)
            if cache is not None:
                cache[slot] = base
        return replace(base, unit=self.styles.unit_style(unit.id))

    def layers_with(self, unit: Unit, preset: str | None, style: Style | None) -> StyleLayers:
        """The unit's stored layers with its owner's preset and style swapped for a candidate (``None`` keeps the stored
        one; an empty ``preset`` takes the preset away). Nothing is saved."""
        base = self.style_layers(unit)
        if preset is None and style is None:
            return base
        chosen = self.styles.preset(preset) if preset and self.styles else None
        return replace(
            base, preset=chosen.style if chosen else (None if preset is not None else base.preset),
            series=style if style is not None else base.series,
        )

    def source_cues(self, unit: Unit) -> list[srt.Cue]:
        path = Path(unit.source_path)
        if not unit.source_path or not path.is_file():
            raise LookupError("the source subtitle of this episode is no longer on disk")
        return srt.parse_file(path)

    def variant_layers(self, unit: Unit, variant: dict[str, Any]) -> StyleLayers:
        """The style layers of a variant (FR-18.8): its preset and style stand in for the series' own and the episode's
        override; the series' character cards stay. Raises ``ValueError`` when the variant's style does not read."""
        base = self.style_layers(unit)
        preset = self.styles.preset(str(variant["preset"])) if self.styles and variant.get("preset") else None
        try:
            style = Style.from_doc(variant.get("style"), "variant.style")
        except StyleError as exc:
            raise ValueError(str(exc)) from None
        return StyleLayers(preset=preset.style if preset else None, series=style, unit=None, cards=base.cards)

    # -- status --------------------------------------------------------------
    def stale_ignored(self, unit: Unit) -> bool:
        if unit.series_id is None:
            return False
        series = self.catalog.get_series(unit.series_id)
        return bool(series and series.stale_policy == "ignore")

    def target_info(
        self, unit: Unit, output: Output, *, glossary: g.Glossary | None = None, cache: dict | None = None,
        styles: StyleLayers | None = None,
    ) -> dict[str, Any]:
        lang, output_path = output.target_lang, output.output_path
        exists = Path(output_path).is_file()
        current = self.versions.repo.current_version(output_path)
        info: dict[str, Any] = {
            "lang": lang,
            "language": output.target_language or lang,
            "exists": exists,
            "status": "untranslated",
            "version": None,
            "trigger": None,
            "model": "",
            "missing_units": [],
            "updated_at": None,
            "modified_outside": False,
            "removed": False,  # the admin deleted it: shown as Missing · Removed and not requested again (FR-16.6)
        }
        if current is None or not exists:
            info["modified_outside"] = exists and current is None  # a file we did not make
            info["removed"] = not exists and self.versions.latest_status(output_path) == "removed"
            return info
        stale = not self.stale_ignored(unit) and (
            current.glossary_hash != self.unit_rules(unit, lang, glossary=glossary, cache=cache, styles=styles).hash
            or (self.memory is not None and self.memory.is_stale(unit, current, cache))
        )
        info.update(
            version=current.version, trigger=current.trigger, model=current.model,
            missing_units=list(current.missing_units),
            updated_at=current.created_at, modified_outside=not self.versions.is_system_file(output_path),
            status="stale" if stale else ("current" if current.context_complete else "incomplete"),
        )
        return info

    def unit_summary(self, unit: Unit, *, cache: dict | None = None) -> dict[str, Any]:
        targets = [self.target_info(unit, out, cache=cache) for out in self.catalog.outputs(unit.id)]
        status = min((t["status"] for t in targets), key=_ATTENTION.__getitem__, default="untranslated")
        return {
            "id": unit.id, "series_id": unit.series_id, "season": unit.season, "episode": unit.episode,
            "title": unit.title, "source_code": unit.source_code, "targets": targets, "status": status,
        }

    # -- listings ------------------------------------------------------------
    def series_list(self) -> list[dict[str, Any]]:
        out = []
        cache: dict = {}
        for series in self.catalog.list_series():
            counts = dict.fromkeys(STATUSES, 0)
            units = self.catalog.list_units(series.id)
            for unit in units:
                counts[self.unit_summary(unit, cache=cache)["status"]] += 1
            out.append({
                "id": series.id, "key": series.key, "title": series.title,
                "stale_policy": series.stale_policy, "units": len(units), "counts": counts,
            })
        return out

    def series_units(self, series_id: int, status: str | None = None) -> dict[str, Any]:
        series = self.catalog.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        if status and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        cache: dict = {}
        units = [self.unit_summary(u, cache=cache) for u in self.catalog.list_units(series_id)]
        languages = sorted({t["lang"] for u in units for t in u["targets"]})
        if status:
            units = [u for u in units if u["status"] == status]
        return {
            "series": {"id": series.id, "key": series.key, "title": series.title,
                       "stale_policy": series.stale_policy},
            "languages": languages,
            "units": units,
        }

    def movies(self, status: str | None = None) -> list[dict[str, Any]]:
        if status and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        cache: dict = {}
        units = [self.unit_summary(u, cache=cache) for u in self.catalog.list_units(movies=True)]
        return [u for u in units if not status or u["status"] == status]

    # -- glossary preview (how many translations a save would make stale) ---------
    def glossary_impact(self, series_id: int, lang: str, candidate: g.Glossary) -> dict[str, int]:
        return self.impact(series_id, lang, glossary=candidate)

    def series_owner(self, series_id: int) -> Owner:
        series = self.catalog.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        return Owner(SERIES, series.key, series.id, series.title, tuple(self.catalog.list_units(series_id)))

    def movie_owner(self, unit_id: int) -> Owner:
        """The owner of a movie's glossary, style and cards, found by the movie's unit id (P12b)."""
        unit = self.catalog.get_unit(unit_id)
        if unit is None or unit.series_id is not None or not unit.movie_key:
            raise LookupError(f"no movie {unit_id}")
        return Owner(MOVIES, str(unit.movie_key), unit.id, unit.title, (unit,))

    def impact(
        self, series_id: int, lang: str, *, glossary: g.Glossary | None = None,
        layers: Callable[[Unit], StyleLayers] | None = None,
    ) -> dict[str, int]:
        """How many translations of a series a candidate glossary, or a candidate set of style layers (``layers``
        answers per unit), would make stale (FR-5.6, FR-18.6). Nothing is saved."""
        return self.impact_units(self.catalog.list_units(series_id), lang, glossary=glossary, layers=layers)

    def impact_units(
        self, units: Iterable[Unit], lang: str | None, *, glossary: g.Glossary | None = None,
        layers: Callable[[Unit], StyleLayers] | None = None,
    ) -> dict[str, int]:
        """The same count for any set of units: a series' episodes, or the one unit of a movie.

        This is the one place that says how many translations a change to the rules would make stale; every save,
        restore, copy, adoption or deletion that changes a rules hash asks it first (FR-5.6, FR-18.6). A glossary is
        written in one language, so a ``glossary`` candidate needs that ``lang``. A style, a preset and a card apply to
        every language of the units, so they pass ``lang=None`` and count the translations in all of them."""
        if glossary is not None and not lang:
            raise ValueError("a glossary is per language: say which one")
        series_cache: dict = {}
        now = after = would = 0
        for unit in units:
            for out in self.catalog.outputs(unit.id):
                if lang and out.target_lang != lang:
                    continue
                before = self.target_info(unit, out, cache=series_cache)["status"]
                later = self.target_info(
                    unit, out, glossary=glossary, styles=layers(unit) if layers else None,
                    cache=series_cache if glossary is None else None,
                )["status"]
                now += before == "stale"
                after += later == "stale"
                would += before != "stale" and later == "stale"
        return {"stale_now": now, "stale_after": after, "would_stale": would}

    def owners_using_preset(self, preset_id: str) -> list[Owner]:
        """The series and movies whose own style names this preset (the ones an edit or a delete of it reaches)."""
        if self.styles is None:
            return []
        found: list[Owner] = []
        for series in self.catalog.list_series():
            if self.styles.owner_style(SERIES, series.key)[0] == preset_id:
                found.append(Owner(SERIES, series.key, series.id, series.title, tuple(self.catalog.list_units(series.id))))
        for unit in self.catalog.list_units(movies=True):
            if unit.movie_key and self.styles.owner_style(MOVIES, str(unit.movie_key))[0] == preset_id:
                found.append(Owner(MOVIES, str(unit.movie_key), unit.id, unit.title, (unit,)))
        return found

    def preset_impact(self, preset_id: str, candidate: Style | None) -> dict[str, int]:
        """What editing a preset (``candidate`` is its new style) or deleting it (``None``: the layer goes) would make
        stale, summed over every series and movie that uses it, in every language. Nothing is saved."""
        total = {"stale_now": 0, "stale_after": 0, "would_stale": 0, "owners": 0}
        for owner in self.owners_using_preset(preset_id):
            counted = self.impact_units(
                owner.units, None, layers=lambda unit: replace(self.style_layers(unit), preset=candidate),
            )
            for name, value in counted.items():
                total[name] += value
            total["owners"] += 1
        return total
