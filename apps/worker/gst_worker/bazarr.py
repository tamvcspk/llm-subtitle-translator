from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import enabled_source_languages, enabled_target_languages
from .http import HTTPClient, first_data


def refresh_bazarr(job: dict[str, Any], http: HTTPClient, bazarr_url: str, api_key: str) -> None:
    headers = {"X-API-KEY": api_key}
    media_type = job.get("media_type")
    if media_type == "series" and job.get("series_id"):
        http.request_json(
            "PATCH",
            f"{bazarr_url}/api/series",
            params={"seriesid": str(job["series_id"]), "action": "scan-disk"},
            headers=headers,
        )
    elif media_type == "movie" and job.get("media_id"):
        http.request_json(
            "PATCH",
            f"{bazarr_url}/api/movies",
            params={"radarrid": str(job["media_id"]), "action": "scan-disk"},
            headers=headers,
        )
    else:
        taskid = "series_full_scan_subtitles" if media_type == "series" else "movies_full_scan_subtitles"
        http.request_json("POST", f"{bazarr_url}/api/system/tasks", params={"taskid": taskid}, headers=headers)


def read_bazarr_api_key(config_path: str) -> str:
    if not config_path or not Path(config_path).exists():
        return ""
    for line in Path(config_path).read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*apikey:\s*(\S+)", line)
        if match:
            return match.group(1).strip("'\"")
    return ""


def find_source_subtitle(
    subtitles: list[dict[str, Any]] | None,
    source_languages: list[dict[str, Any]],
) -> dict[str, str]:
    if not isinstance(subtitles, list):
        return {}
    sources = enabled_source_languages({"source_languages": source_languages})
    for source in sources:
        wanted = str(source["code"]).lower()
        for subtitle in subtitles:
            code = str(subtitle.get("code2") or subtitle.get("code") or "").lower()
            if code != wanted:
                continue
            return {
                "path": str(subtitle.get("path") or ""),
                "code": str(source["code"]),
                "language": str(source["language"]),
            }
    return {}


def missing_enabled_targets(item: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing = item.get("missing_subtitles")
    if not isinstance(missing, list):
        return []
    missing_codes = {str(entry.get("code2") or "").lower() for entry in missing}
    return [target for target in enabled_target_languages({"target_languages": targets}) if str(target["code"]).lower() in missing_codes]


def list_wanted_items(
    http: HTTPClient,
    bazarr_url: str,
    api_key: str,
    source_languages: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    limit: int = 100,
) -> list[dict[str, Any]]:
    headers = {"X-API-KEY": api_key}
    items: list[dict[str, Any]] = []

    episodes = http.get_json(
        f"{bazarr_url}/api/episodes/wanted",
        params={"start": 0, "length": limit},
        headers=headers,
    ).get("data", [])
    for wanted in episodes:
        wanted_targets = missing_enabled_targets(wanted, targets)
        if not wanted_targets:
            continue
        episode_id = str(wanted.get("sonarrEpisodeId") or "")
        series_id = str(wanted.get("sonarrSeriesId") or "")
        episode = first_data(
            http.get_json(
                f"{bazarr_url}/api/episodes",
                params={"episodeid[]": episode_id},
                headers=headers,
            )
        )
        source_subtitle = find_source_subtitle(episode.get("subtitles"), source_languages)
        subtitle_path = source_subtitle.get("path", "")
        source_code = source_subtitle.get("code", "")
        source_language = source_subtitle.get("language", "")
        wanted_targets = [target for target in wanted_targets if str(target["code"]).lower() != source_code.lower()]
        if not wanted_targets:
            continue
        items.append(
            {
                "type": "series",
                "title": f"{wanted.get('seriesTitle', '')} {wanted.get('episode_number', '')}".strip(),
                "subtitle_path": subtitle_path,
                "video_path": episode.get("path") or "",
                "source_code": source_code,
                "source_language": source_language,
                "series_id": series_id,
                "media_id": episode_id,
                "missing_targets": wanted_targets,
                "can_enqueue": bool(subtitle_path and Path(subtitle_path).exists()),
            }
        )

    movies = http.get_json(
        f"{bazarr_url}/api/movies/wanted",
        params={"start": 0, "length": limit},
        headers=headers,
    ).get("data", [])
    for wanted in movies:
        wanted_targets = missing_enabled_targets(wanted, targets)
        if not wanted_targets:
            continue
        movie_id = str(wanted.get("radarrId") or wanted.get("radarrid") or "")
        movie = first_data(
            http.get_json(
                f"{bazarr_url}/api/movies",
                params={"radarrid[]": movie_id},
                headers=headers,
            )
        )
        source_subtitle = find_source_subtitle(movie.get("subtitles"), source_languages)
        subtitle_path = source_subtitle.get("path", "")
        source_code = source_subtitle.get("code", "")
        source_language = source_subtitle.get("language", "")
        wanted_targets = [target for target in wanted_targets if str(target["code"]).lower() != source_code.lower()]
        if not wanted_targets:
            continue
        items.append(
            {
                "type": "movie",
                "title": wanted.get("title") or movie.get("title") or "",
                "subtitle_path": subtitle_path,
                "video_path": movie.get("path") or "",
                "source_code": source_code,
                "source_language": source_language,
                "media_id": movie_id,
                "series_id": "",
                "missing_targets": wanted_targets,
                "can_enqueue": bool(subtitle_path and Path(subtitle_path).exists()),
            }
        )
    return items


class BazarrIntegration:
    """Narrow interface for wanted discovery and subtitle refresh."""
    def __init__(self, http: HTTPClient, bazarr_url: str, api_key: str) -> None:
        self.http, self.bazarr_url, self.api_key = http, bazarr_url.rstrip("/"), api_key
    def wanted(self, source_languages: list[dict[str, Any]], targets: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
        return list_wanted_items(self.http, self.bazarr_url, self.api_key, source_languages, targets, limit)
    def refresh(self, job: dict[str, Any]) -> None:
        refresh_bazarr(job, self.http, self.bazarr_url, self.api_key)
