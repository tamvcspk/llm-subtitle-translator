from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_LANGUAGES = [{"code": "en", "language": "English", "enabled": True}]
DEFAULT_TARGET_LANGUAGES = [{"code": "zh", "language": "Simplified Chinese", "enabled": True}]
FALLBACK_LANGUAGES = [
    {"name": "Chinese Simplified", "code2": "zh", "code3": "zho", "enabled": True},
    {"name": "Chinese Traditional", "code2": "zt", "code3": "zht", "enabled": True},
    {"name": "English", "code2": "en", "code3": "eng", "enabled": True},
    {"name": "Japanese", "code2": "ja", "code3": "jpn", "enabled": True},
    {"name": "Korean", "code2": "ko", "code3": "kor", "enabled": True},
    {"name": "Spanish", "code2": "es", "code3": "spa", "enabled": True},
    {"name": "French", "code2": "fr", "code3": "fra", "enabled": True},
    {"name": "German", "code2": "de", "code3": "deu", "enabled": True},
]
GEMINI_LANGUAGE_NAMES = {
    "Chinese Simplified": "Simplified Chinese",
    "Chinese Traditional": "Traditional Chinese",
}
SECRET_MASK = "**********"
SECRET_CONFIG_KEYS = ("bazarr_api_key", "gemini_api_key", "gemini_api_key2", "tmdb_api_key")
GST_BOOL_CONFIG_KEYS = (
    "gst_paid_quota",
    "gst_skip_upgrade",
    "gst_quiet",
    "gst_progress_log",
    "gst_thoughts_log",
    "gst_token_report",
    "gst_no_streaming",
    "gst_no_thinking",
)
GST_STRING_CONFIG_KEYS = (
    "gst_model",
    "gst_fallback_model",
    "gst_temperature",
    "gst_top_p",
    "gst_top_k",
    "gst_thinking_budget",
    "gst_thinking_level",
)
DEFAULT_APP_CONFIG = {
    "source_languages": DEFAULT_SOURCE_LANGUAGES,
    "target_languages": DEFAULT_TARGET_LANGUAGES,
    "media_roots": ["/media"],
    "scan_limit": 200,
    "bazarr_url": "",
    "bazarr_api_key": "",
    "gemini_api_key": "",
    "gemini_api_key2": "",
    "tmdb_api_key": "",
    "gst_model": "gemini-flash-latest",
    "gst_fallback_model": "",
    "gst_batch_size": 500,
    "gst_retry_batch_size": 300,
    "gst_paid_quota": False,
    "gst_skip_upgrade": True,
    "gst_quiet": True,
    "gst_progress_log": False,
    "gst_thoughts_log": False,
    "gst_token_report": False,
    "gst_temperature": "0.7",
    "gst_top_p": "0.95",
    "gst_top_k": "40",
    "gst_thinking_budget": "2048",
    "gst_thinking_level": "medium",
    "gst_no_streaming": True,
    "gst_no_thinking": False,
    "gst_context_size": 50,
    "job_settle_seconds": 600,
}


def enabled_languages(config: dict[str, Any], key: str, default: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = config.get(key) if isinstance(config, dict) else None
    if not isinstance(entries, list):
        return [dict(item) for item in default]

    enabled: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or entry.get("code2") or "").strip()
        language = str(entry.get("language") or entry.get("name") or "").strip()
        if not code or not language:
            continue
        if entry.get("enabled", True) is False:
            continue
        enabled.append({"code": code, "language": language, "enabled": True})
    return enabled or [dict(item) for item in default]


def enabled_source_languages(config: dict[str, Any]) -> list[dict[str, Any]]:
    return enabled_languages(config, "source_languages", DEFAULT_SOURCE_LANGUAGES)


def enabled_target_languages(config: dict[str, Any]) -> list[dict[str, Any]]:
    return enabled_languages(config, "target_languages", DEFAULT_TARGET_LANGUAGES)


def normalize_app_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in DEFAULT_APP_CONFIG.items():
        if isinstance(value, list):
            normalized[key] = [dict(item) if isinstance(item, dict) else item for item in value]
        else:
            normalized[key] = value
    if isinstance(config, dict):
        normalized.update({key: value for key, value in config.items() if key in normalized})
    normalized["source_languages"] = enabled_source_languages(normalized)
    normalized["target_languages"] = enabled_target_languages(normalized)
    media_roots = normalized.get("media_roots")
    if not isinstance(media_roots, list) or not media_roots:
        normalized["media_roots"] = ["/media"]
    else:
        normalized["media_roots"] = [str(root) for root in media_roots if str(root).strip()] or ["/media"]
    normalized["bazarr_url"] = str(normalized.get("bazarr_url") or "").strip().rstrip("/")
    for key in SECRET_CONFIG_KEYS:
        normalized[key] = str(normalized.get(key) or "").strip()
    for key in GST_STRING_CONFIG_KEYS:
        normalized[key] = str(normalized.get(key) or "").strip()
    if not normalized["gst_model"]:
        normalized["gst_model"] = str(DEFAULT_APP_CONFIG["gst_model"])
    if normalized["gst_fallback_model"] == normalized["gst_model"]:
        normalized["gst_fallback_model"] = ""
    if normalized["gst_thinking_level"] not in {"", "minimal", "low", "medium", "high"}:
        normalized["gst_thinking_level"] = ""
    for key in GST_BOOL_CONFIG_KEYS:
        normalized[key] = bool(normalized.get(key))
    try:
        normalized["gst_batch_size"] = max(1, int(normalized.get("gst_batch_size", 500)))
    except (TypeError, ValueError):
        normalized["gst_batch_size"] = 500
    try:
        normalized["gst_retry_batch_size"] = max(0, int(normalized.get("gst_retry_batch_size", 300)))
    except (TypeError, ValueError):
        normalized["gst_retry_batch_size"] = 300
    try:
        normalized["gst_context_size"] = max(0, int(normalized.get("gst_context_size", 50)))
    except (TypeError, ValueError):
        normalized["gst_context_size"] = 50
    try:
        normalized["job_settle_seconds"] = max(0, int(normalized.get("job_settle_seconds", 600)))
    except (TypeError, ValueError):
        normalized["job_settle_seconds"] = 600
    try:
        normalized["scan_limit"] = max(1, int(normalized.get("scan_limit", 200)))
    except (TypeError, ValueError):
        normalized["scan_limit"] = 200
    return normalized


def load_app_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return normalize_app_config({})
    try:
        return normalize_app_config(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logging.warning("Ignoring unreadable app config: %s", path)
        return normalize_app_config({})


def save_app_config(
    config_path: str,
    config: dict[str, Any],
    postprocess_targets_path: str | None = None,
) -> dict[str, Any]:
    path = Path(config_path)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    merged = dict(config)
    for key in SECRET_CONFIG_KEYS:
        value = str(merged.get(key) or "").strip()
        if (not value or value == SECRET_MASK) and existing.get(key):
            merged[key] = existing[key]
    normalized = normalize_app_config(merged)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

    if postprocess_targets_path:
        targets_path = Path(postprocess_targets_path)
        targets_path.parent.mkdir(parents=True, exist_ok=True)
        targets_tmp = targets_path.with_suffix(f"{targets_path.suffix}.tmp")
        targets_tmp.write_text(
            json.dumps(
                {
                    "source_languages": enabled_source_languages(normalized),
                    "target_languages": enabled_target_languages(normalized),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        targets_tmp.replace(targets_path)
    return normalized


def normalize_bazarr_language(item: dict[str, Any]) -> dict[str, Any] | None:
    code = str(item.get("code2") or item.get("code") or "").strip()
    name = str(item.get("name") or item.get("language") or "").strip()
    if not code or not name:
        return None
    return {
        "code": code,
        "code3": str(item.get("code3") or "").strip(),
        "name": name,
        "language": GEMINI_LANGUAGE_NAMES.get(name, name),
        "enabled_in_bazarr": bool(item.get("enabled")),
    }


def supported_languages(http: Any, bazarr_url: str, api_key: str) -> list[dict[str, Any]]:
    try:
        raw = http.get_json(f"{bazarr_url}/api/system/languages", headers={"X-API-KEY": api_key})
    except Exception as exc:
        logging.warning("Falling back to bundled language list: %s", exc)
        raw = FALLBACK_LANGUAGES

    if not isinstance(raw, list):
        raw = FALLBACK_LANGUAGES

    languages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = normalize_bazarr_language(item)
        if not normalized:
            continue
        code = normalized["code"].lower()
        if code in seen:
            continue
        seen.add(code)
        languages.append(normalized)
    return languages


class ConfigurationStore:
    """Deep interface for normalized configuration persistence."""

    def __init__(self, path: str, postprocess_targets_path: str | None = None) -> None:
        self.path = path
        self.postprocess_targets_path = postprocess_targets_path

    def load(self) -> dict[str, Any]:
        return load_app_config(self.path)

    def save(self, config: dict[str, Any]) -> dict[str, Any]:
        return save_app_config(self.path, config, self.postprocess_targets_path)
