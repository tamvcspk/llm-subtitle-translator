from __future__ import annotations

import logging
import re
from typing import Any, Callable

from subcore.memory import clean_cast_name

from ..domain.ports import Cast
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


class TmdbMetadata:
    """The :class:`~subserver.domain.ports.Metadata` port over TMDB and Bazarr. Reads the live settings on every call,
    so a key saved in the console takes effect without a restart."""

    def __init__(self, http: HTTPClient, cache: MemoryCache, settings_source: Callable[[], dict[str, Any]]) -> None:
        self.http = http
        self.cache = cache
        self._settings = settings_source

    def description(self, job: dict[str, Any]) -> str:
        settings = self._settings()
        return build_tmdb_description(
            job,
            bazarr=self.http,
            tmdb=self.http,
            bazarr_url=str(settings.get("bazarr_url") or ""),
            bazarr_api_key=str(settings.get("bazarr_api_key") or ""),
            tmdb_api_key=str(settings.get("tmdb_api_key") or ""),
            cache=self.cache,
        )


    def cast(self, job: dict[str, Any]) -> Cast:
        settings = self._settings()
        return build_tmdb_cast(
            job, bazarr=self.http, tmdb=self.http, bazarr_url=str(settings.get("bazarr_url") or ""),
            bazarr_api_key=str(settings.get("bazarr_api_key") or ""), tmdb_api_key=str(settings.get("tmdb_api_key") or ""),
            cache=self.cache,
        )

    def genres(self, job: dict[str, Any]) -> tuple[str, ...]:
        settings = self._settings()
        return build_tmdb_genres(
            job, bazarr=self.http, tmdb=self.http, bazarr_url=str(settings.get("bazarr_url") or ""),
            bazarr_api_key=str(settings.get("bazarr_api_key") or ""), tmdb_api_key=str(settings.get("tmdb_api_key") or ""),
            cache=self.cache,
        )


def _characters(entry: dict[str, Any]) -> list[str]:
    """The character names of one cast entry: an aggregate credit has ``roles``, an episode's guest star ``character``."""
    names = [str(r.get("character") or "") for r in entry.get("roles") or [] if isinstance(r, dict)]
    names.append(str(entry.get("character") or ""))
    out: list[str] = []
    for raw in names:
        for part in raw.split("/"):  # "Mom / Lois" is two roles in one string
            name = clean_cast_name(part)
            if name and name not in out:
                out.append(name)
    return out


def build_tmdb_cast(
    job: dict[str, Any], bazarr: HTTPClient, tmdb: HTTPClient, bazarr_url: str, bazarr_api_key: str, tmdb_api_key: str,
    cache: MemoryCache, main_limit: int = 20,
) -> Cast:
    """The character names TMDB lists for a series and one of its episodes (P19b, FR-5.9.4): the main cast with the
    number of episodes each is in (``/tv/{id}/aggregate_credits``) and the episode's guest stars. Empty when there is no
    key, no match, or the lookup failed: a cast list only narrows the prompt and is never an error. A movie has none."""
    if not tmdb_api_key or job.get("media_type") != "series":
        return Cast()
    try:
        headers = {"X-API-KEY": bazarr_api_key}
        series = first_data(bazarr.get_json(
            f"{bazarr_url}/api/series", params={"seriesid[]": str(job.get("series_id", ""))}, headers=headers,
        ))
        tv_id = tmdb_find_tv_id(series, tmdb, tmdb_api_key, cache)
        if not tv_id:
            return Cast()
        credits = cached_get_json(
            cache, tmdb, f"{TMDB_BASE_URL}/tv/{tv_id}/aggregate_credits", {"api_key": tmdb_api_key, "language": "en-US"},
        )
        ranked: list[tuple[int, int, str]] = []
        for order, entry in enumerate(credits.get("cast", []) if isinstance(credits, dict) else []):
            if not isinstance(entry, dict):
                continue
            episodes = int(entry.get("total_episode_count") or max((int(r.get("episode_count") or 0) for r in entry.get("roles") or [] if isinstance(r, dict)), default=0))
            for name in _characters(entry):
                ranked.append((-episodes, order, name))
        seen: set[str] = set()
        main: list[tuple[str, int]] = []
        for neg, _, name in sorted(ranked):
            if name.casefold() not in seen:
                seen.add(name.casefold())
                main.append((name, -neg))
        episode = first_data(bazarr.get_json(
            f"{bazarr_url}/api/episodes", params={"episodeid[]": str(job.get("media_id", ""))}, headers=headers,
        ))
        guests: list[str] = []
        season, number = int(episode.get("season") or 0), int(episode.get("episode") or 0)
        if season and number:
            detail = cached_get_json(
                cache, tmdb, f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season}/episode/{number}",
                {"api_key": tmdb_api_key, "language": "en-US"},
            )
            for entry in detail.get("guest_stars", []) if isinstance(detail, dict) else []:
                if isinstance(entry, dict):
                    guests.extend(n for n in _characters(entry) if n not in guests)
        return Cast(tuple(main[:main_limit]), tuple(guests))
    except Exception:  # noqa: BLE001
        logging.exception("Could not read the TMDB cast for %s", job.get("series_id") or job.get("video_path"))
        return Cast()


def build_tmdb_genres(
    job: dict[str, Any], bazarr: HTTPClient, tmdb: HTTPClient, bazarr_url: str, bazarr_api_key: str, tmdb_api_key: str,
    cache: MemoryCache,
) -> tuple[str, ...]:
    """TMDB's genre names of a series or a movie, to suggest a style preset (FR-18.4). Empty when there is no key, no
    match or the lookup failed: a suggestion is a convenience and never an error."""
    if not tmdb_api_key:
        return ()
    try:
        if job.get("media_type") == "series":
            series = first_data(bazarr.get_json(
                f"{bazarr_url}/api/series", params={"seriesid[]": str(job.get("series_id", ""))},
                headers={"X-API-KEY": bazarr_api_key},
            ))
            tv_id = tmdb_find_tv_id(series, tmdb, tmdb_api_key, cache)
            path = f"/tv/{tv_id}" if tv_id else ""
        else:
            movie_id = extract_tmdb_movie_id(str(job.get("video_path", "")))
            path = f"/movie/{movie_id}" if movie_id else ""
        if not path:
            return ()
        show = cached_get_json(cache, tmdb, f"{TMDB_BASE_URL}{path}", {"api_key": tmdb_api_key, "language": "en-US"})
        return tuple(str(g["name"]) for g in show.get("genres", []) if g.get("name"))
    except Exception:  # noqa: BLE001
        logging.exception("Could not read TMDB genres for %s", job.get("series_id") or job.get("video_path"))
        return ()


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
