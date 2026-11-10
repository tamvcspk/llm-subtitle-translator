from __future__ import annotations

import logging
import re
from typing import Any

from .http import HTTPClient, MemoryCache, cached_get_json, first_data


TMDB_BASE_URL = "https://api.themoviedb.org/3"


def extract_tmdb_movie_id(path: str) -> str:
    match = re.search(r"\{tmdb-(\d+)\}", path)
    return match.group(1) if match else ""


def tmdb_find_movie_id(imdb_id: str, tmdb: HTTPClient, tmdb_api_key: str, cache: MemoryCache) -> str:
    if not imdb_id:
        return ""
    response = cached_get_json(
        cache,
        tmdb,
        f"{TMDB_BASE_URL}/find/{imdb_id}",
        {"api_key": tmdb_api_key, "external_source": "imdb_id"},
    )
    results = response.get("movie_results", []) if isinstance(response, dict) else []
    return str(results[0].get("id", "")) if results else ""


def tmdb_find_tv_id(series: dict[str, Any], tmdb: HTTPClient, tmdb_api_key: str, cache: MemoryCache) -> str:
    tvdb_id = str(series.get("tvdbId") or "")
    if tvdb_id:
        response = cached_get_json(
            cache,
            tmdb,
            f"{TMDB_BASE_URL}/find/{tvdb_id}",
            {"api_key": tmdb_api_key, "external_source": "tvdb_id"},
        )
        results = response.get("tv_results", []) if isinstance(response, dict) else []
        if results:
            return str(results[0].get("id", ""))

    imdb_id = str(series.get("imdbId") or "")
    if imdb_id:
        response = cached_get_json(
            cache,
            tmdb,
            f"{TMDB_BASE_URL}/find/{imdb_id}",
            {"api_key": tmdb_api_key, "external_source": "imdb_id"},
        )
        results = response.get("tv_results", []) if isinstance(response, dict) else []
        if results:
            return str(results[0].get("id", ""))
    return ""


def build_tmdb_description(
    job: dict[str, Any],
    bazarr: HTTPClient,
    tmdb: HTTPClient,
    bazarr_url: str,
    bazarr_api_key: str,
    tmdb_api_key: str,
    cache: MemoryCache,
) -> str:
    if not tmdb_api_key:
        return ""
    try:
        if job.get("media_type") == "series":
            return build_series_description(job, bazarr, tmdb, bazarr_url, bazarr_api_key, tmdb_api_key, cache)
        return build_movie_description(job, bazarr, tmdb, bazarr_url, bazarr_api_key, tmdb_api_key, cache)
    except Exception:
        logging.exception("Failed to build TMDB description for %s", job.get("subtitle_path"))
        return ""


def build_movie_description(
    job: dict[str, Any],
    bazarr: HTTPClient,
    tmdb: HTTPClient,
    bazarr_url: str,
    bazarr_api_key: str,
    tmdb_api_key: str,
    cache: MemoryCache,
) -> str:
    movie_id = extract_tmdb_movie_id(str(job.get("video_path", "")))
    if not movie_id and job.get("media_id"):
        movie = first_data(
            bazarr.get_json(
                f"{bazarr_url}/api/movies",
                params={"radarrid[]": str(job["media_id"])},
                headers={"X-API-KEY": bazarr_api_key},
            )
        )
        movie_id = tmdb_find_movie_id(str(movie.get("imdbId") or ""), tmdb, tmdb_api_key, cache)
    if not movie_id:
        return ""

    movie = cached_get_json(
        cache,
        tmdb,
        f"{TMDB_BASE_URL}/movie/{movie_id}",
        {"api_key": tmdb_api_key, "language": "en-US"},
    )
    title = movie.get("title") or movie.get("name") or ""
    year = str(movie.get("release_date") or "")[:4]
    genres = ", ".join(g.get("name", "") for g in movie.get("genres", []) if g.get("name"))
    overview = movie.get("overview") or ""
    return f"Overview: {overview}\n\n{title} - {year}\nGenre(s): {genres}".strip()


def build_series_description(
    job: dict[str, Any],
    bazarr: HTTPClient,
    tmdb: HTTPClient,
    bazarr_url: str,
    bazarr_api_key: str,
    tmdb_api_key: str,
    cache: MemoryCache,
) -> str:
    headers = {"X-API-KEY": bazarr_api_key}
    series = first_data(
        bazarr.get_json(
            f"{bazarr_url}/api/series",
            params={"seriesid[]": str(job.get("series_id", ""))},
            headers=headers,
        )
    )
    episode = first_data(
        bazarr.get_json(
            f"{bazarr_url}/api/episodes",
            params={"episodeid[]": str(job.get("media_id", ""))},
            headers=headers,
        )
    )
    tv_id = tmdb_find_tv_id(series, tmdb, tmdb_api_key, cache)
    if not tv_id:
        return ""

    show = cached_get_json(cache, tmdb, f"{TMDB_BASE_URL}/tv/{tv_id}", {"api_key": tmdb_api_key, "language": "en-US"})
    season = int(episode.get("season") or 0)
    episode_number = int(episode.get("episode") or 0)
    tmdb_episode = cached_get_json(
        cache,
        tmdb,
        f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season}/episode/{episode_number}",
        {"api_key": tmdb_api_key, "language": "en-US"},
    )
    show_title = show.get("name") or series.get("title") or ""
    episode_title = tmdb_episode.get("name") or episode.get("title") or ""
    episode_overview = tmdb_episode.get("overview") or ""
    show_overview = show.get("overview") or series.get("overview") or ""
    return (
        f"Episode Overview: {episode_overview}\n\n"
        f"{show_title} S{season:02d}E{episode_number:02d} - {episode_title}\n"
        f"Show Overview: {show_overview}"
    ).strip()
