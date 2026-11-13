"""The library: series, episodes and movies the worker has met, and where each translation stands (FR-10).

A unit (one episode or one movie) is registered when a job for it runs. Its identity comes from the
job (Bazarr ids) and, failing that, from the file name; Bazarr is only asked for the title, season and
episode when the worker has the API key (`enrich_job_identity`).

A translation is ``stale`` when it was made with different rules than the ones that apply now (glossary
file or episode corrections changed; FR-5.5). Staleness is only ever flagged, never acted on (FR-5.6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from subcore import glossary as g
from subcore import srt

from .glossaries import MOVIES, SERIES, GlossaryFiles
from .rules import UnitRules, build_rules
from .store import Store
from .versions import TranslationVersions

STATUSES = ("current", "stale", "incomplete", "untranslated")
_ATTENTION = {"stale": 0, "incomplete": 1, "untranslated": 2, "current": 3}

_EPISODE = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})")
_X_EPISODE = re.compile(r"(?<![\dA-Za-z])(\d{1,2})x(\d{2,3})(?!\d)")
_SEASON_DIR = re.compile(r"^(season|series|staffel|saison|temporada)\s*\d+$|^s\d{1,2}$", re.IGNORECASE)


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
        if job.get("tvdb_id"):
            key = f"tvdb-{job['tvdb_id']}"
        elif job.get("series_id"):
            key = f"bazarr-{job['series_id']}"
        else:
            key = f"title-{_slug(title) or 'unknown'}"
        return Identity(series_key=key, series_title=title or key, season=season, episode=episode,
                        title=str(job.get("episode_title") or "").strip())

    title = str(job.get("movie_title") or "").strip() or _clean_title(stem)
    tmdb = re.search(r"\{tmdb-(\d+)\}", video)
    if tmdb:
        key = f"tmdb-{tmdb.group(1)}"
    elif media_type == "movie" and job.get("media_id"):
        key = f"bazarr-movie-{job['media_id']}"
    else:
        key = "path-" + hashlib.sha1((video or subtitle).encode("utf-8")).hexdigest()[:12]
    return Identity(movie_key=key, title=title or key)


def enrich_job_identity(job: dict[str, Any], http: Any, bazarr_url: str, api_key: str) -> None:
    """Add Bazarr's title/season/episode to ``job`` (best effort; the file name is the fallback)."""
    if not api_key or not job.get("media_id"):
        return
    from .http import first_data

    headers = {"X-API-KEY": api_key}
    try:
        if job.get("media_type") == "series" and job.get("series_id"):
            series = first_data(http.get_json(f"{bazarr_url}/api/series", params={"seriesid[]": str(job["series_id"])}, headers=headers))
            episode = first_data(http.get_json(f"{bazarr_url}/api/episodes", params={"episodeid[]": str(job["media_id"])}, headers=headers))
            if series.get("title"):
                job["series_title"] = str(series["title"])
            if series.get("tvdbId"):
                job["tvdb_id"] = str(series["tvdbId"])
            if episode.get("season") is not None and episode.get("episode") is not None:
                job["season"], job["episode"] = int(episode["season"]), int(episode["episode"])
            if episode.get("title"):
                job["episode_title"] = str(episode["title"])
        elif job.get("media_type") == "movie":
            movie = first_data(http.get_json(f"{bazarr_url}/api/movies", params={"radarrid[]": str(job["media_id"])}, headers=headers))
            if movie.get("title"):
                job["movie_title"] = str(movie["title"])
    except Exception as exc:  # noqa: BLE001 - identity is a convenience; never fail a job over it
        logging.warning("Could not read media details from Bazarr (%s); using the file name", type(exc).__name__)


class Library:
    def __init__(
        self, store: Store, glossaries: GlossaryFiles, versions: TranslationVersions, memory: Any = None
    ) -> None:
        self.store = store
        self.glossaries = glossaries
        self.versions = versions
        self.memory = memory  # StoryMemory | None: when set, story memory also decides staleness (FR-5.5)

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
                series_id = self.store.upsert_series(
                    ident.series_key, title=ident.series_title, bazarr_id=str(job.get("series_id") or "")
                )
                unit_id = self.store.upsert_unit(
                    series_id=series_id, season=ident.season, episode=ident.episode, **common
                )
            else:
                unit_id = self.store.upsert_unit(movie_key=ident.movie_key, **common)
        output = str(job.get("output_path") or "")
        if output and job.get("target_code"):
            self.store.set_output(unit_id, str(job["target_code"]), output, str(job.get("target_language") or ""))
        return unit_id

    # -- rules ---------------------------------------------------------------
    def owner(self, unit: dict[str, Any]) -> tuple[str, str]:
        if unit["series_id"] is not None:
            series = self.store.get_series(int(unit["series_id"]))
            return SERIES, str(series["key"])
        return MOVIES, str(unit["movie_key"])

    def unit_rules(
        self,
        unit: dict[str, Any],
        lang: str,
        *,
        cues: list[srt.Cue] | None = None,
        correction_ids: list[int] | None = None,
        glossary: g.Glossary | None = None,
        cache: dict | None = None,
    ) -> UnitRules:
        """Rules for one unit and language; ``correction_ids`` narrows the episode corrections used."""
        kind, key = self.owner(unit)
        if glossary is None:
            slot = (kind, key, lang)
            if cache is not None and slot in cache:
                glossary = cache[slot]
            else:
                glossary = self.glossaries.load(kind, key, lang)
                if cache is not None:
                    cache[slot] = glossary
        corrections = self.store.corrections(int(unit["id"]), lang)
        if correction_ids is not None:
            wanted = set(correction_ids)
            corrections = [c for c in corrections if c["kind"] == "cue_edit" or c["id"] in wanted]
        return build_rules(
            glossary, season=unit["season"], episode=unit["episode"], corrections=corrections, cues=cues
        )

    def source_cues(self, unit: dict[str, Any]) -> list[srt.Cue]:
        path = Path(str(unit["source_path"]))
        if not unit["source_path"] or not path.is_file():
            raise LookupError("the source subtitle of this episode is no longer on disk")
        return srt.parse_file(path)

    # -- status --------------------------------------------------------------
    def stale_ignored(self, unit: dict[str, Any]) -> bool:
        if unit["series_id"] is None:
            return False
        series = self.store.get_series(int(unit["series_id"]))
        return bool(series and series["stale_policy"] == "ignore")

    def target_info(
        self, unit: dict[str, Any], output: dict[str, Any], *, glossary: g.Glossary | None = None, cache: dict | None = None
    ) -> dict[str, Any]:
        lang, output_path = str(output["target_lang"]), str(output["output_path"])
        exists = Path(output_path).is_file()
        current = self.store.current_version(output_path)
        info: dict[str, Any] = {
            "lang": lang,
            "language": output["target_language"] or lang,
            "exists": exists,
            "status": "untranslated",
            "version": None,
            "trigger": None,
            "model": "",
            "missing_units": [],
            "updated_at": None,
            "modified_outside": False,
        }
        if current is None or not exists:
            info["modified_outside"] = exists and current is None  # a file we did not make
            return info
        stale = not self.stale_ignored(unit) and (
            current["glossary_hash"] != self.unit_rules(unit, lang, glossary=glossary, cache=cache).hash
            or (self.memory is not None and self.memory.is_stale(unit, current, cache))
        )
        info.update(
            version=current["version"], trigger=current["trigger"], model=current["model"],
            missing_units=json.loads(current["missing_units_json"] or "[]"),
            updated_at=current["created_at"], modified_outside=not self.versions.is_system_file(output_path),
            status="stale" if stale else ("current" if current["context_complete"] else "incomplete"),
        )
        return info

    def unit_summary(self, unit: dict[str, Any], *, cache: dict | None = None) -> dict[str, Any]:
        targets = [self.target_info(unit, out, cache=cache) for out in self.store.outputs(int(unit["id"]))]
        status = min((t["status"] for t in targets), key=_ATTENTION.__getitem__, default="untranslated")
        return {
            "id": unit["id"], "series_id": unit["series_id"], "season": unit["season"], "episode": unit["episode"],
            "title": unit["title"], "source_code": unit["source_code"], "targets": targets, "status": status,
        }

    # -- listings ------------------------------------------------------------
    def series_list(self) -> list[dict[str, Any]]:
        out = []
        cache: dict = {}
        for series in self.store.list_series():
            counts = dict.fromkeys(STATUSES, 0)
            units = self.store.list_units(int(series["id"]))
            for unit in units:
                counts[self.unit_summary(unit, cache=cache)["status"]] += 1
            out.append({
                "id": series["id"], "key": series["key"], "title": series["title"],
                "stale_policy": series["stale_policy"], "units": len(units), "counts": counts,
            })
        return out

    def series_units(self, series_id: int, status: str | None = None) -> dict[str, Any]:
        series = self.store.get_series(series_id)
        if series is None:
            raise LookupError(f"no series {series_id}")
        if status and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        cache: dict = {}
        units = [self.unit_summary(u, cache=cache) for u in self.store.list_units(series_id)]
        languages = sorted({t["lang"] for u in units for t in u["targets"]})
        if status:
            units = [u for u in units if u["status"] == status]
        return {
            "series": {"id": series["id"], "key": series["key"], "title": series["title"],
                       "stale_policy": series["stale_policy"]},
            "languages": languages,
            "units": units,
        }

    def movies(self, status: str | None = None) -> list[dict[str, Any]]:
        if status and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        cache: dict = {}
        units = [self.unit_summary(u, cache=cache) for u in self.store.list_units(movies=True)]
        return [u for u in units if not status or u["status"] == status]

    # -- glossary preview (how many translations a save would make stale) ---------
    def glossary_impact(self, series_id: int, lang: str, candidate: g.Glossary) -> dict[str, int]:
        series_cache: dict = {}
        now = after = would = 0
        for unit in self.store.list_units(series_id):
            for out in self.store.outputs(int(unit["id"])):
                if out["target_lang"] != lang:
                    continue
                before = self.target_info(unit, out, cache=series_cache)["status"]
                later = self.target_info(unit, out, glossary=candidate)["status"]
                now += before == "stale"
                after += later == "stale"
                would += before != "stale" and later == "stale"
        return {"stale_now": now, "stale_after": after, "would_stale": would}
