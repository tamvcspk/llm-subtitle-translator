from __future__ import annotations

from typing import Any

from .http import HTTPClient
from .tmdb import TMDB_BASE_URL


GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def test_connection(kind: str, settings: dict[str, str], http: HTTPClient) -> dict[str, Any]:
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
        return {"ok": False, "kind": kind, "message": str(exc)}
