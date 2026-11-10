from __future__ import annotations

import logging

from .http import HTTPClient


GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
FALLBACK_GEMINI_MODELS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


def model_item(model_id: str) -> dict[str, str]:
    return {"id": model_id, "name": model_id}


def fallback_gemini_models() -> list[dict[str, str]]:
    return [model_item(model_id) for model_id in FALLBACK_GEMINI_MODELS]


def gemini_models(http: HTTPClient, settings: dict[str, str]) -> list[dict[str, str]]:
    api_key = str(settings.get("gemini_api_key") or "")
    if not api_key:
        return fallback_gemini_models()
    try:
        response = http.get_json(GEMINI_MODELS_URL, params={"key": api_key})
    except Exception:
        logging.warning("Falling back to bundled Gemini model list", exc_info=True)
        return fallback_gemini_models()

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in response.get("models", []) if isinstance(response, dict) else []:
        if not isinstance(entry, dict):
            continue
        methods = entry.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        model_id = str(entry.get("name") or "").removeprefix("models/").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        items.append(model_item(model_id))
    return items or fallback_gemini_models()
