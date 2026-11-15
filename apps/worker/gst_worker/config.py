from __future__ import annotations

import logging
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
# Two model layers: the cheap default does the work; the strong model (setting `gst_fallback_model`)
# only takes jobs the default cannot finish: output that stays invalid, an outage, or spent quota.
DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_STRONG_MODEL = "gemini-3.5-flash"
SECRET_MASK = "**********"
SECRET_CONFIG_KEYS = ("bazarr_api_key", "gemini_api_key", "gemini_api_key2", "tmdb_api_key", "openai_compat_api_key")
# The form field of each secret -> (provider, position among that provider's stored keys). The console edits the
# keys through these four fields; /api/keys works on every stored key (FR-20.2).
KEY_SLOTS = {
    "bazarr_api_key": ("bazarr", 0),
    "tmdb_api_key": ("tmdb", 0),
    "gemini_api_key": ("gemini", 0),
    "gemini_api_key2": ("gemini", 1),
    "openai_compat_api_key": ("openai_compat", 0),
}
# Configuration of the process itself (paths, ports, the console account): read from the environment when the
# worker starts and never stored in, or overwritten from, the settings table (FR-20.1).
SYSTEM_SETTING_KEYS = frozenset({
    "state_dir", "queue_dir", "data_dir", "db_path", "log_dir", "static_dir", "config_path",
    "postprocess_targets_path", "web_host", "web_port", "console_user", "console_password", "console_auth",
})
# Settings the in-process engine reads. Options of the old `gst` CLI (batch sizes, top_p/top_k, thinking
# level, streaming, context size, logging switches) were dropped in P3: they changed nothing.
GST_BOOL_CONFIG_KEYS = (
    "gst_paid_quota",
    "gst_no_thinking",
)
GST_STRING_CONFIG_KEYS = (
    "gst_model",
    "gst_fallback_model",
    "gst_temperature",
    "gst_thinking_budget",
    "llm_provider",
    "llm_base_url",
    "research_model",
)
# Which adapter the models run on (FR-9.1): Gemini, or any OpenAI-compatible server at `llm_base_url`
# (OpenRouter, Ollama, LM Studio, vLLM). One provider at a time; every task and both model layers use it.
PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI_COMPAT = "openai_compat"
LLM_PROVIDERS = (PROVIDER_GEMINI, PROVIDER_OPENAI_COMPAT)
# What an OpenAI-compatible server cannot tell us about its model: set them to the model's real limits.
# key -> (default, minimum)
LLM_INT_CONFIG_KEYS = {
    "llm_max_input_tokens": (128_000, 1_000),
    "llm_max_output_tokens": (8_192, 256),
}
# Story memory (P4). Off by default until its relationship extraction has been measured on real series
# (ARCHITECTURE section 8); a series can override the switch in the console.
MEMORY_BOOL_CONFIG_KEYS = ("memory_enabled",)
# `memory_model` reads the facts (task `extract`); `summarize_model` condenses old summaries (task `summarize`) and
# falls back to `memory_model`, then to `gst_model` (FR-9.3).
MEMORY_STRING_CONFIG_KEYS = ("memory_model", "summarize_model")
# key -> (default, minimum)
MEMORY_INT_CONFIG_KEYS = {
    "memory_gap_wait_seconds": (1800, 0),
    "memory_budget_tokens": (8000, 500),
    "memory_recent_episodes": (3, 0),
}
# How the worker receives the jobs Bazarr's script queues (FR-12): `auto` runs them, `hold` sets them aside
# until the admin approves. Jobs the admin makes in the console are never held.
ADMISSION_AUTO = "auto"
ADMISSION_HOLD = "hold"
ADMISSION_MODES = (ADMISSION_AUTO, ADMISSION_HOLD)
# Logging and measurement (P5, FR-19). `log_prompt_bodies` is what is kept of the prompts and answers of model
# calls: `off`, `errors` (failed calls only), `sample` (failed calls and one call in ten) or `all`.
LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_BODY_MODES = ("off", "errors", "sample", "all")
# key -> (default, minimum); 0 turns a limit off where the key is a limit
LOG_INT_CONFIG_KEYS = {
    "log_body_max_bytes": (262_144, 1_024),
    "log_max_mb": (500, 0),
    "log_retention_days": (30, 0),
    "events_retention_days": (90, 0),
}
# Price of a million tokens in the admin's own currency; 0 = no cost estimate on the call records.
LOG_FLOAT_CONFIG_KEYS = ("llm_cost_in_per_mtok", "llm_cost_out_per_mtok")
DEFAULT_APP_CONFIG = {
    "source_languages": DEFAULT_SOURCE_LANGUAGES,
    "target_languages": DEFAULT_TARGET_LANGUAGES,
    "media_roots": ["/media"],
    "scan_limit": 200,
    "bazarr_url": "",
    "bazarr_api_key": "",
    "gemini_api_key": "",
    "gemini_api_key2": "",
    "openai_compat_api_key": "",
    "tmdb_api_key": "",
    "gst_model": DEFAULT_MODEL,
    "gst_fallback_model": DEFAULT_STRONG_MODEL,
    "gst_paid_quota": False,
    "gst_temperature": "0.7",
    "gst_thinking_budget": "2048",
    "gst_no_thinking": False,
    "llm_provider": PROVIDER_GEMINI,
    "llm_base_url": "",
    "research_model": "",
    **{key: default for key, (default, _) in LLM_INT_CONFIG_KEYS.items()},
    "job_settle_seconds": 600,
    "admission_mode": ADMISSION_AUTO,
    "memory_enabled": False,
    "memory_model": "",
    "summarize_model": "",
    **{key: default for key, (default, _) in MEMORY_INT_CONFIG_KEYS.items()},
    "log_level": "INFO",
    "log_prompt_bodies": "all",
    **{key: default for key, (default, _) in LOG_INT_CONFIG_KEYS.items()},
    **{key: 0.0 for key in LOG_FLOAT_CONFIG_KEYS},
}


def provider_name(settings: dict[str, Any]) -> str:
    """The adapter the models run on: ``gemini`` unless the settings say ``openai_compat``."""
    return PROVIDER_OPENAI_COMPAT if str(settings.get("llm_provider") or "") == PROVIDER_OPENAI_COMPAT else PROVIDER_GEMINI


def model_for_task(settings: dict[str, Any], task: str, default: str = DEFAULT_MODEL) -> str:
    """The model a task runs on (FR-9.3). ``translate`` uses ``gst_model``; ``extract`` the cheaper
    ``memory_model``; ``summarize`` its own ``summarize_model``, else ``memory_model``; ``research``
    ``research_model``. Each falls back to the translation model, so a single model setting is enough."""
    def named(key: str) -> str:
        return str(settings.get(key) or "").strip()

    main = named("gst_model") or default
    if task == "extract":
        return named("memory_model") or main
    if task == "summarize":
        return named("summarize_model") or named("memory_model") or main
    if task == "research":
        return named("research_model") or main
    return main


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
    normalized["llm_provider"] = normalized["llm_provider"].lower()
    if normalized["llm_provider"] not in LLM_PROVIDERS:
        normalized["llm_provider"] = PROVIDER_GEMINI
    normalized["llm_base_url"] = normalized["llm_base_url"].rstrip("/")
    for key, (default, minimum) in LLM_INT_CONFIG_KEYS.items():
        try:
            normalized[key] = max(minimum, int(normalized.get(key, default)))
        except (TypeError, ValueError):
            normalized[key] = default
    if not normalized["gst_model"]:
        normalized["gst_model"] = str(DEFAULT_APP_CONFIG["gst_model"])
    if normalized["gst_fallback_model"] == normalized["gst_model"]:
        normalized["gst_fallback_model"] = ""
    for key in GST_BOOL_CONFIG_KEYS:
        normalized[key] = bool(normalized.get(key))
    try:
        normalized["job_settle_seconds"] = max(0, int(normalized.get("job_settle_seconds", 600)))
    except (TypeError, ValueError):
        normalized["job_settle_seconds"] = 600
    normalized["admission_mode"] = str(normalized.get("admission_mode") or "").strip().lower()
    if normalized["admission_mode"] not in ADMISSION_MODES:
        normalized["admission_mode"] = ADMISSION_AUTO
    for key in MEMORY_BOOL_CONFIG_KEYS:
        normalized[key] = bool(normalized.get(key))
    for key in MEMORY_STRING_CONFIG_KEYS:
        normalized[key] = str(normalized.get(key) or "").strip()
    for key, (default, minimum) in MEMORY_INT_CONFIG_KEYS.items():
        try:
            normalized[key] = max(minimum, int(normalized.get(key, default)))
        except (TypeError, ValueError):
            normalized[key] = default
    normalized["log_level"] = str(normalized.get("log_level") or "").strip().upper()
    if normalized["log_level"] not in LOG_LEVEL_NAMES:
        normalized["log_level"] = "INFO"
    normalized["log_prompt_bodies"] = str(normalized.get("log_prompt_bodies") or "").strip().lower()
    if normalized["log_prompt_bodies"] not in LOG_BODY_MODES:
        normalized["log_prompt_bodies"] = "all"
    for key, (default, minimum) in LOG_INT_CONFIG_KEYS.items():
        try:
            normalized[key] = max(minimum, int(normalized.get(key, default)))
        except (TypeError, ValueError):
            normalized[key] = default
    for key in LOG_FLOAT_CONFIG_KEYS:
        try:
            normalized[key] = max(0.0, float(normalized.get(key) or 0.0))
        except (TypeError, ValueError):
            normalized[key] = 0.0
    try:
        normalized["scan_limit"] = max(1, int(normalized.get("scan_limit", 200)))
    except (TypeError, ValueError):
        normalized["scan_limit"] = 200
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
