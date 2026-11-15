from __future__ import annotations

import urllib.error
from typing import Any

from .http import HTTPClient, describe_http_error
from .tmdb import TMDB_BASE_URL


GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Google answers a key it does not know with 400, not 401; Bazarr and TMDB use 401/403.
_REFUSED_CODES = {"gemini_api_key": (400, 401, 403), "gemini_api_key2": (400, 401, 403)}
_DEFAULT_REFUSED_CODES = (401, 403)


def key_was_refused(kind: str, exc: BaseException) -> bool:
    """True when the service answered that the key itself is wrong (not a network or server problem)."""
    return isinstance(exc, urllib.error.HTTPError) and exc.code in _REFUSED_CODES.get(kind, _DEFAULT_REFUSED_CODES)


def test_connection(kind: str, settings: dict[str, str], http: HTTPClient) -> dict[str, Any]:
    """Ask the service whether the key works. ``auth_failed`` is set only when the service refused the key, so a
    caller that keeps a key's status can tell a bad key from an unreachable service."""
    try:
        if kind == "bazarr":
            bazarr_url = str(settings.get("bazarr_url") or "").rstrip("/")
            api_key = str(settings.get("bazarr_api_key") or "")
            if not bazarr_url or not api_key:
                return {"ok": False, "kind": kind, "message": "Bazarr URL or API key is empty"}
            http.get_json(f"{bazarr_url}/api/system/languages", headers={"X-API-KEY": api_key})
            return {"ok": True, "kind": kind, "message": "Bazarr API key is valid"}

        if kind in {"gemini_api_key", "gemini_api_key2"}:
            api_key = str(settings.get(kind) or "")
            if not api_key:
                return {"ok": False, "kind": kind, "message": "Gemini API key is empty"}
            http.get_json(GEMINI_MODELS_URL, params={"key": api_key})
            return {"ok": True, "kind": kind, "message": "Gemini API key is valid"}

        if kind == "tmdb_api_key":
            api_key = str(settings.get("tmdb_api_key") or "")
            if not api_key:
                return {"ok": False, "kind": kind, "message": "TMDB API key is empty"}
            http.get_json(f"{TMDB_BASE_URL}/configuration", params={"api_key": api_key})
            return {"ok": True, "kind": kind, "message": "TMDB API key is valid"}

        return {"ok": False, "kind": kind, "message": "Unknown test target"}
    except Exception as exc:
        service = {"bazarr": "Bazarr", "tmdb_api_key": "TMDB"}.get(kind, "Gemini")
        message = describe_http_error(exc, service)
        if key_was_refused(kind, exc) and isinstance(exc, urllib.error.HTTPError) and exc.code == 400:
            message = f"{service} rejected the API key (HTTP 400)."
        return {"ok": False, "kind": kind, "message": message, "auth_failed": key_was_refused(kind, exc)}
