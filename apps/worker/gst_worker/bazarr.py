from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from .config import enabled_source_languages, enabled_target_languages
from .http import HTTPClient, first_data
from .ports import EpisodeListing, MediaDetails


# Bazarr answers a scan-disk call only when it has scanned the whole series: 31 s for a series of 150 episodes,
# well past the default HTTP timeout.
REFRESH_TIMEOUT_SECONDS = 180


def refresh_bazarr(job: dict[str, Any], http: HTTPClient, bazarr_url: str, api_key: str) -> None:
    headers = {"X-API-KEY": api_key}
    media_type = job.get("media_type")
    if media_type == "series" and job.get("series_id"):
        http.request_json(
            "PATCH",
            f"{bazarr_url}/api/series",
            params={"seriesid": str(job["series_id"]), "action": "scan-disk"},
            headers=headers,
            timeout=REFRESH_TIMEOUT_SECONDS,
        )
    elif media_type == "movie" and job.get("media_id"):
        http.request_json(
            "PATCH",
            f"{bazarr_url}/api/movies",
            params={"radarrid": str(job["media_id"]), "action": "scan-disk"},
            headers=headers,
            timeout=REFRESH_TIMEOUT_SECONDS,
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


def target_still_missing(job: dict[str, Any], http: HTTPClient, bazarr_url: str, api_key: str) -> bool | None:
    """Does Bazarr itself still list the job's target language as missing for this item?

    ``False`` means Bazarr has the language (or does not want it): do not translate. ``None`` means
    Bazarr cannot say (no ids on the job, no key, unreachable, unexpected answer); the caller then
    relies on the files on disk.
    """
    media_id = str(job.get("media_id") or "")
    if not media_id or not api_key or not bazarr_url:
        return None
    headers = {"X-API-KEY": api_key}
    try:
        if job.get("media_type") == "series":
            item = first_data(http.get_json(f"{bazarr_url}/api/episodes", params={"episodeid[]": media_id}, headers=headers))
        elif job.get("media_type") == "movie":
            item = first_data(http.get_json(f"{bazarr_url}/api/movies", params={"radarrid[]": media_id}, headers=headers))
        else:
            return None
    except Exception as exc:  # unreachable Bazarr must not stop translation that the files say is needed
        logging.warning("Could not ask Bazarr whether %s is missing: %s", job.get("target_code"), exc)
        return None
    missing = item.get("missing_subtitles") if isinstance(item, dict) else None
    if not isinstance(missing, list):
        return None
    codes = {str(entry.get("code2") or "").lower() for entry in missing if isinstance(entry, dict)}
    return str(job.get("target_code") or "").lower() in codes


def describe_media(
    http: HTTPClient, bazarr_url: str, api_key: str, media_type: str, series_id: str, media_id: str
) -> MediaDetails | None:
    """Title, season and episode of an item from Bazarr; None when there is nothing to ask about."""
    if not api_key or not media_id:
        return None
    headers = {"X-API-KEY": api_key}
    if media_type == "series" and series_id:
        series = first_data(http.get_json(f"{bazarr_url}/api/series", params={"seriesid[]": series_id}, headers=headers))
        episode = first_data(http.get_json(f"{bazarr_url}/api/episodes", params={"episodeid[]": media_id}, headers=headers))
        has_numbers = episode.get("season") is not None and episode.get("episode") is not None
        return MediaDetails(
            series_title=str(series.get("title") or ""),
            tvdb_id=str(series.get("tvdbId") or ""),
            season=int(episode["season"]) if has_numbers else None,
            episode=int(episode["episode"]) if has_numbers else None,
            episode_title=str(episode.get("title") or ""),
        )
    if media_type == "movie":
        movie = first_data(http.get_json(f"{bazarr_url}/api/movies", params={"radarrid[]": media_id}, headers=headers))
        return MediaDetails(movie_title=str(movie.get("title") or ""))
    return None


def list_series_episodes(
    http: HTTPClient, bazarr_url: str, api_key: str, series_id: str, source_code: str
) -> list[EpisodeListing]:
    """Every episode Bazarr lists for a series, with its source subtitle if there is one (FR-8.3).

    ``source_path`` is empty for an episode with no external subtitle in ``source_code``; the worker treats
    such an episode as a gap in the story memory until a subtitle appears.
    """
    response = http.get_json(
        f"{bazarr_url}/api/episodes", params={"seriesid[]": str(series_id)}, headers={"X-API-KEY": api_key}
    )
    data = response.get("data") if isinstance(response, dict) else None
    wanted = [{"code": source_code, "language": source_code}] if source_code else []
    episodes: list[EpisodeListing] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or item.get("season") is None or item.get("episode") is None:
            continue
        found = find_source_subtitle(item.get("subtitles"), wanted) if wanted else {}
        episodes.append(EpisodeListing(
            season=int(item["season"]),
            episode=int(item["episode"]),
            title=str(item.get("title") or ""),
            bazarr_id=str(item.get("sonarrEpisodeId") or ""),
            video_path=str(item.get("path") or ""),
            source_path=found.get("path", ""),
        ))
    return episodes


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


class BazarrMedia:
    """The :class:`~gst_worker.ports.MediaLibrary` port over Bazarr. It reads the live settings on every call,
    so a URL or key saved in the console takes effect without a restart."""

    def __init__(self, http: HTTPClient, settings_source: Callable[[], dict[str, Any]]) -> None:
        self.http = http
        self._settings = settings_source

    def _access(self) -> tuple[str, str]:
        settings = self._settings()
        return str(settings.get("bazarr_url") or "").rstrip("/"), str(settings.get("bazarr_api_key") or "")

    def describe(self, media_type: str, series_id: str, media_id: str) -> MediaDetails | None:
        url, key = self._access()
        return describe_media(self.http, url, key, media_type, series_id, media_id)

    def series_episodes(self, series_id: str, source_code: str) -> list[EpisodeListing]:
        url, key = self._access()
        if not key or not series_id:
            return []
        return list_series_episodes(self.http, url, key, series_id, source_code)

    def target_still_missing(self, job: dict[str, Any]) -> bool | None:
        url, key = self._access()
        return target_still_missing(job, self.http, url, key)

    def refresh(self, job: dict[str, Any]) -> None:
        url, key = self._access()
        refresh_bazarr(job, self.http, url, key)


class BazarrIntegration:
    """Narrow interface for wanted discovery and subtitle refresh."""
    def __init__(self, http: HTTPClient, bazarr_url: str, api_key: str) -> None:
        self.http, self.bazarr_url, self.api_key = http, bazarr_url.rstrip("/"), api_key
    def wanted(self, source_languages: list[dict[str, Any]], targets: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
        return list_wanted_items(self.http, self.bazarr_url, self.api_key, source_languages, targets, limit)
    def refresh(self, job: dict[str, Any]) -> None:
        refresh_bazarr(job, self.http, self.bazarr_url, self.api_key)
    def series_episodes(self, series_id: str, source_code: str) -> list[EpisodeListing]:
        return list_series_episodes(self.http, self.bazarr_url, self.api_key, series_id, source_code)
