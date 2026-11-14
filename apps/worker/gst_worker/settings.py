"""Runtime settings: the app config file layered over environment variables and Bazarr's own config.

An adapter module (it reads the environment and files); the core receives the result as a plain mapping.
"""
from __future__ import annotations

import os
from typing import Any

from .bazarr import read_bazarr_api_key
from .config import (
    DEFAULT_MODEL,
    GST_BOOL_CONFIG_KEYS,
    GST_STRING_CONFIG_KEYS,
    MEMORY_BOOL_CONFIG_KEYS,
    MEMORY_INT_CONFIG_KEYS,
    MEMORY_STRING_CONFIG_KEYS,
    SECRET_CONFIG_KEYS,
    SECRET_MASK,
    load_app_config,
    normalize_app_config,
    save_app_config,
)


def load_settings(config_path: str | None = None) -> dict[str, Any]:
    app_config = load_app_config(config_path) if config_path else normalize_app_config({})
    bazarr_config_path = os.getenv("BAZARR_CONFIG_PATH", "/bazarr-config/config.yaml")
    bazarr_url = (
        str(app_config.get("bazarr_url") or "").strip()
        or os.getenv("BAZARR_URL", "http://bazarr:6767")
    ).rstrip("/")
    settings: dict[str, Any] = {
        "bazarr_url": bazarr_url,
        "bazarr_api_key": (
            str(app_config.get("bazarr_api_key") or "").strip()
            or os.getenv("BAZARR_API_KEY")
            or read_bazarr_api_key(bazarr_config_path)
        ),
        "gemini_api_key": (
            str(app_config.get("gemini_api_key") or "").strip()
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GEMINI_API_KEY1")
            or ""
        ),
        "gemini_api_key2": str(app_config.get("gemini_api_key2") or "").strip() or os.getenv("GEMINI_API_KEY2", ""),
        "tmdb_api_key": str(app_config.get("tmdb_api_key") or "").strip() or os.getenv("TMDB_API_KEY", ""),
    }
    for key in GST_STRING_CONFIG_KEYS:
        settings[key] = str(app_config.get(key) or "").strip()
    for key in GST_BOOL_CONFIG_KEYS:
        settings[key] = bool(app_config.get(key))
    settings["gst_model"] = str(app_config.get("gst_model") or os.getenv("GST_MODEL", DEFAULT_MODEL))
    settings["job_settle_seconds"] = int(app_config.get("job_settle_seconds") or 0)
    for key in MEMORY_BOOL_CONFIG_KEYS:
        settings[key] = bool(app_config.get(key))
    for key in MEMORY_STRING_CONFIG_KEYS:
        settings[key] = str(app_config.get(key) or "").strip()
    for key in MEMORY_INT_CONFIG_KEYS:
        settings[key] = int(app_config[key])
    for key in ("llm_rpm", "llm_tpm", "llm_rpd"):
        if os.getenv(key.upper(), "").strip():
            settings[key] = os.getenv(key.upper(), "").strip()
    return settings


def public_app_config(config_path: str, settings: dict[str, str] | None = None) -> dict[str, Any]:
    runtime_settings = settings or load_settings(config_path)
    config = load_app_config(config_path)
    config["bazarr_url"] = runtime_settings["bazarr_url"]
    for key in SECRET_CONFIG_KEYS:
        config[key] = SECRET_MASK if runtime_settings.get(key, "") else ""
        config[f"{key}_configured"] = bool(runtime_settings.get(key, ""))
    return config


def settings_from_payload(config_path: str, body: dict[str, Any]) -> dict[str, str]:
    settings = load_settings(config_path)
    bazarr_url = str(body.get("bazarr_url") or "").strip().rstrip("/")
    if bazarr_url:
        settings["bazarr_url"] = bazarr_url
    for key in SECRET_CONFIG_KEYS:
        value = str(body.get(key) or "").strip()
        if value and value != SECRET_MASK:
            settings[key] = value
    return settings


def seed_app_config_from_settings(
    config_path: str,
    settings: dict[str, str],
    postprocess_targets_path: str | None = None,
) -> bool:
    config = load_app_config(config_path)
    changed = False
    if not config.get("bazarr_url") and settings.get("bazarr_url"):
        config["bazarr_url"] = settings["bazarr_url"]
        changed = True
    for key in SECRET_CONFIG_KEYS:
        if not config.get(key) and settings.get(key):
            config[key] = settings[key]
            changed = True
    if changed:
        save_app_config(config_path, config, postprocess_targets_path)
    return changed
