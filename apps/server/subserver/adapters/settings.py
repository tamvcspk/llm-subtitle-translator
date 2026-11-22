"""Console settings and API keys (FR-20, ADR-13): the SQLite tables layered over environment variables.

An adapter module (it reads the environment, files and Bazarr's own config); the core receives the result as a
plain mapping. Order of precedence for a setting: the stored value, then the environment, then the default.
Nothing here needs a restart: every read goes to the tables, so a change is live as soon as it is saved.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .bazarr import read_bazarr_api_key
from ..domain.config import (
    CATALOG_BOOL_CONFIG_KEYS,
    CATALOG_INT_CONFIG_KEYS,
    DEFAULT_APP_CONFIG,
    DEFAULT_MODEL,
    GST_BOOL_CONFIG_KEYS,
    GST_STRING_CONFIG_KEYS,
    JUDGE_BOOL_CONFIG_KEYS,
    KEY_SLOTS,
    LLM_INT_CONFIG_KEYS,
    LLM_PROVIDERS,
    LOG_FLOAT_CONFIG_KEYS,
    LOG_INT_CONFIG_KEYS,
    MEMORY_BOOL_CONFIG_KEYS,
    MEMORY_INT_CONFIG_KEYS,
    MEMORY_STRING_CONFIG_KEYS,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI_COMPAT,
    QUEUE_INT_CONFIG_KEYS,
    RETENTION_INT_CONFIG_KEYS,
    SECRET_CONFIG_KEYS,
    SECRET_MASK,
    STYLE_INT_CONFIG_KEYS,
    SUGGESTION_BOOL_CONFIG_KEYS,
    SYSTEM_SETTING_KEYS,
    enabled_source_languages,
    enabled_target_languages,
    normalize_app_config,
)
from ..domain.ports import KEY_EXHAUSTED, KEY_INVALID, KEY_OK, KEY_STATUSES, ApiKey, Telemetry
from .store import Store
from ..domain.redaction import DEFAULT as DEFAULT_REDACTOR

# Markers the worker leaves in the settings table (keys that start with "_" are never shown or edited).
IMPORTED_MARK = "_config_json_imported"
SEEDED_MARK = "_keys_seeded"
KEY_PROVIDERS = ("gemini", PROVIDER_OPENAI_COMPAT, "bazarr", "tmdb")
# Providers that can hold several keys, which are then numbered ("Key 1", "Key 2").
NUMBERED_PROVIDERS = ("gemini", PROVIDER_OPENAI_COMPAT)
# A key shorter than this shows no tail at all: four characters of a short secret are too much of it.
LAST4_MIN_LENGTH = 12
TOUCH_INTERVAL_SECONDS = 30.0
# Environment variables that seed the keys on the first run, in the order of the console's four fields.
SEED_ENV = {
    "bazarr_api_key": ("BAZARR_API_KEY",),
    "tmdb_api_key": ("TMDB_API_KEY",),
    "gemini_api_key": ("GEMINI_API_KEY", "GEMINI_API_KEY1"),
    "gemini_api_key2": ("GEMINI_API_KEY2",),
    "openai_compat_api_key": ("LLM_API_KEY", "OPENAI_API_KEY"),
}
SLOT_LABELS = {"bazarr": "Bazarr", "tmdb": "TMDB", "gemini": "Key", PROVIDER_OPENAI_COMPAT: "Key"}


def last_chars(value: str) -> str:
    """The only part of a key the API ever returns (FR-20.3); nothing for a short key."""
    value = str(value or "")
    return value[-4:] if len(value) >= LAST4_MIN_LENGTH else ""


def key_view(key: ApiKey) -> dict[str, Any]:
    """A stored key as the API shows it: no value, only its last four characters."""
    return {
        "id": key.id, "provider": key.provider, "label": key.label, "status": key.status,
        "last4": last_chars(key.value), "last_used_at": key.last_used_at, "updated_at": key.updated_at,
    }


class SettingsService:
    """Reads and writes the console settings and the API keys, and tells subscribers when they change."""

    def __init__(
        self,
        repo: Store,
        *,
        config_path: str = "",
        postprocess_targets_path: str = "",
        env: Mapping[str, str] | None = None,
        telemetry: Telemetry | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repo = repo
        self.config_path = config_path
        self.postprocess_targets_path = postprocess_targets_path
        self.env = os.environ if env is None else env
        self.telemetry = telemetry
        self._clock = clock
        self._subscribers: list[Callable[[], None]] = []
        self._touched: dict[int, float] = {}
        self._lock = threading.RLock()
        self.model_warnings: dict[str, str] = {}  # model -> what the provider said (ModelNotFound)

    # -- start-up ---------------------------------------------------------------
    def start(self) -> None:
        """Import config.json and seed the keys from the environment, each only once; then publish the targets."""
        with self._lock:
            marks = {row.key for row in self.repo.settings_rows()}
            legacy = self._read_legacy_config()
            if IMPORTED_MARK not in marks:
                self._import_legacy(legacy)
            if SEEDED_MARK not in marks:
                self._seed_keys(legacy)
            self._register_secrets()
            self._write_targets()

    def _read_legacy_config(self) -> dict[str, Any]:
        path = Path(self.config_path) if self.config_path else None
        if path is None or not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logging.warning("Ignoring unreadable app config: %s", path)
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _import_legacy(self, legacy: dict[str, Any]) -> None:
        """Take the values of config.json that the table does not have yet (a value already stored wins)."""
        stored = {row.key for row in self.repo.settings_rows()}
        raw = {
            key: value for key, value in legacy.items()
            if self._editable(key) and key not in stored and value is not None and value != ""
        }
        values = {key: value for key, value in normalize_app_config(raw).items() if key in raw}
        self.repo.setting_save({**values, IMPORTED_MARK: self._clock()})
        if values:
            logging.info("Imported %d settings from %s into the database", len(values), self.config_path)

    def _seed_keys(self, legacy: dict[str, Any]) -> None:
        """First run only: the keys of config.json, else of the environment, become rows. Later changes to the
        environment never reach them (FR-20.2)."""
        have = {(key.provider, key.value) for key in self.repo.api_keys()}
        for field, (provider, position) in KEY_SLOTS.items():
            value = str(legacy.get(field) or "").strip()
            for name in SEED_ENV[field]:
                value = value or str(self.env.get(name) or "").strip()
            if value and (provider, value) not in have:
                self.repo.api_key_add(provider, self._label(provider, position), value)
                have.add((provider, value))
        self.repo.setting_save({SEEDED_MARK: self._clock()})

    @staticmethod
    def _editable(key: str) -> bool:
        return key in DEFAULT_APP_CONFIG and key not in SECRET_CONFIG_KEYS and key not in SYSTEM_SETTING_KEYS

    @staticmethod
    def _label(provider: str, position: int) -> str:
        base = SLOT_LABELS.get(provider, provider)
        return f"{base} {position + 1}" if provider in NUMBERED_PROVIDERS else base

    # -- settings ---------------------------------------------------------------
    def stored(self) -> dict[str, Any]:
        """The values the console has saved (and the importer brought in), by key."""
        return {row.key: row.value for row in self.repo.settings_rows() if self._editable(row.key)}

    def app_config(self) -> dict[str, Any]:
        """The normalized configuration without any secret: defaults under what is stored."""
        return normalize_app_config(self.stored())

    def target_languages(self) -> list[dict[str, Any]]:
        return enabled_target_languages(self.app_config())

    def settings(self) -> dict[str, Any]:
        """What the worker runs on: stored value, else environment, else default (the keys included)."""
        app = self.app_config()
        env = self.env
        keys = self.repo.api_keys()
        by_provider = {name: [key for key in keys if key.provider == name] for name in KEY_PROVIDERS}
        usable_gemini = [key.value for key in by_provider["gemini"] if key.status == KEY_OK]
        usable_openai = [key.value for key in by_provider[PROVIDER_OPENAI_COMPAT] if key.status == KEY_OK]
        stored = self.stored()

        def first(provider: str) -> str:
            rows = by_provider[provider]
            return rows[0].value if rows else ""

        bazarr_config_path = str(env.get("BAZARR_CONFIG_PATH") or "/bazarr-config/config.yaml")
        settings: dict[str, Any] = {
            "bazarr_url": (str(app.get("bazarr_url") or "").strip() or str(env.get("BAZARR_URL") or "http://bazarr:6767")).rstrip("/"),
            "bazarr_api_key": first("bazarr") or str(env.get("BAZARR_API_KEY") or "") or read_bazarr_api_key(bazarr_config_path),
            "gemini_api_key": usable_gemini[0] if usable_gemini else "",
            "gemini_api_key2": usable_gemini[1] if len(usable_gemini) > 1 else "",
            "tmdb_api_key": first("tmdb") or str(env.get("TMDB_API_KEY") or ""),
            "openai_compat_api_key": usable_openai[0] if usable_openai else "",
        }
        for key in GST_STRING_CONFIG_KEYS:
            settings[key] = str(app.get(key) or "").strip()
        for key in GST_BOOL_CONFIG_KEYS:
            settings[key] = bool(app.get(key))
        # stored value, else GST_MODEL, else the default (the normalized config always holds the default)
        stored_model = str(stored.get("gst_model") or "").strip()
        settings["gst_model"] = stored_model or str(env.get("GST_MODEL") or "").strip() or DEFAULT_MODEL
        # The provider follows the same order: what the console saved, else LLM_PROVIDER / LLM_BASE_URL, else Gemini.
        provider = (str(stored.get("llm_provider") or "").strip() or str(env.get("LLM_PROVIDER") or "").strip()
                    or PROVIDER_GEMINI).lower()
        settings["llm_provider"] = provider if provider in LLM_PROVIDERS else PROVIDER_GEMINI
        settings["llm_base_url"] = (
            str(stored.get("llm_base_url") or "").strip() or str(env.get("LLM_BASE_URL") or "").strip()
        ).rstrip("/")
        for key in LLM_INT_CONFIG_KEYS:
            settings[key] = int(app[key])
        settings["job_settle_seconds"] = int(app.get("job_settle_seconds") or 0)
        for key in CATALOG_BOOL_CONFIG_KEYS:
            settings[key] = bool(app.get(key))
        for key in CATALOG_INT_CONFIG_KEYS:
            settings[key] = int(app[key])
        # The switches of suggestions and of the judge: without them here the running worker reads each one's default
        # and a switch turned off in the console would change nothing.
        for key in (*SUGGESTION_BOOL_CONFIG_KEYS, *JUDGE_BOOL_CONFIG_KEYS):
            settings[key] = bool(app.get(key))
        for key in MEMORY_BOOL_CONFIG_KEYS:
            settings[key] = bool(app.get(key))
        for key in MEMORY_STRING_CONFIG_KEYS:
            settings[key] = str(app.get(key) or "").strip()
        for key in MEMORY_INT_CONFIG_KEYS:
            settings[key] = int(app[key])
        settings["log_level"] = str(app["log_level"])
        settings["log_prompt_bodies"] = str(app["log_prompt_bodies"])
        for key in LOG_INT_CONFIG_KEYS:
            settings[key] = int(app[key])
        for key in LOG_FLOAT_CONFIG_KEYS:
            settings[key] = float(app[key])
        for key in (*QUEUE_INT_CONFIG_KEYS, *STYLE_INT_CONFIG_KEYS, *RETENTION_INT_CONFIG_KEYS):
            settings[key] = int(app[key])
        settings["quota_reset_tz"] = str(app["quota_reset_tz"])
        for key in ("llm_rpm", "llm_tpm", "llm_rpd"):
            if str(env.get(key.upper()) or "").strip():
                settings[key] = str(env.get(key.upper())).strip()
        return settings

    def save(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Store the settings in ``body`` (and any key in the four secret fields) and tell the subscribers.

        Only keys the console owns are taken: a system key (a path, the port, the console account) is ignored.
        A secret field that is blank or still the mask leaves the stored key as it is."""
        if not isinstance(body, Mapping):
            raise ValueError("settings must be a JSON object")
        with self._lock:
            incoming = {key: value for key, value in body.items() if self._editable(key)}
            merged = normalize_app_config({**self.stored(), **incoming})
            names = set(incoming)
            if names & {"gst_model", "gst_fallback_model"}:
                names.add("gst_fallback_model")  # the two are normalized together
            if names:
                self.repo.setting_save({name: merged[name] for name in names})
            for field in SECRET_CONFIG_KEYS:
                if field in body:
                    self._set_slot(field, str(body[field] or "").strip())
            self._register_secrets()
            self._write_targets()
        self._notify()
        return self.app_config()

    def with_payload(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """The runtime settings with the values typed in the form laid over them, nothing saved (used to test a
        key or an address before it is stored). The mask means "the stored key"."""
        settings = self.settings()
        bazarr_url = str(body.get("bazarr_url") or "").strip().rstrip("/")
        if bazarr_url:
            settings["bazarr_url"] = bazarr_url
        for field, (provider, position) in KEY_SLOTS.items():
            value = str(body.get(field) or "").strip()
            if value and value != SECRET_MASK:
                settings[field] = value
            else:  # the stored key, even one marked invalid: that is the one being retested
                row = self._slot(provider, position)
                if row is not None:
                    settings[field] = row.value
        return settings

    def public(self, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """The configuration for the console: secrets masked, with what each stored key can safely say."""
        runtime = settings or self.settings()
        config = self.app_config()
        config["bazarr_url"] = runtime["bazarr_url"]
        for field, (provider, position) in KEY_SLOTS.items():
            row = self._slot(provider, position)
            configured = row is not None or bool(runtime.get(field))
            config[field] = SECRET_MASK if configured else ""
            config[f"{field}_configured"] = configured
            config[f"{field}_last4"] = last_chars(row.value) if row else ""
            config[f"{field}_status"] = row.status if row else ""
        config["key_warnings"] = self.warnings()
        return config

    def export_config(self) -> dict[str, Any]:
        """The settings for a backup: everything the console saved, no key (FR-20.3)."""
        return self.app_config()

    def export_keys(self) -> list[dict[str, Any]]:
        """The keys with their values, for a backup that was asked to hold them (the only place a value leaves)."""
        return [
            {"provider": key.provider, "label": key.label, "value": key.value, "status": key.status}
            for key in self.repo.api_keys()
        ]

    def import_config(self, config: Mapping[str, Any], keys: list[Mapping[str, Any]] | None = None) -> None:
        """Restore a backup. Its settings replace the stored ones; its keys (when the backup has them) are added;
        a key a pre-P6 backup holds in the four fields fills a slot that is empty. The console account is
        never touched (ADR-7)."""
        with self._lock:
            values = {key: value for key, value in config.items() if self._editable(key)}
            merged = normalize_app_config({**self.stored(), **values})
            if values:
                self.repo.setting_save({key: merged[key] for key in values})
            have = {(key.provider, key.value) for key in self.repo.api_keys()}
            for item in keys or []:
                provider, value = str(item.get("provider") or ""), str(item.get("value") or "").strip()
                if provider in KEY_PROVIDERS and value and (provider, value) not in have:
                    status = str(item.get("status") or KEY_OK)
                    self.repo.api_key_add(
                        provider, str(item.get("label") or self._label(provider, 0)),
                        value, status if status in KEY_STATUSES else KEY_OK,
                    )
                    have.add((provider, value))
            for field in SECRET_CONFIG_KEYS:
                value = str(config.get(field) or "").strip()
                provider, position = KEY_SLOTS[field]
                if value and value != SECRET_MASK and self._slot(provider, position) is None \
                        and (provider, value) not in have:
                    self.repo.api_key_add(provider, self._label(provider, position), value)
            self._register_secrets()
            self._write_targets()
        self._notify()

    def _write_targets(self) -> None:
        """The language list Bazarr's post-processing script reads (``targets.json``)."""
        if not self.postprocess_targets_path:
            return
        app = self.app_config()
        path = Path(self.postprocess_targets_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"source_languages": enabled_source_languages(app), "target_languages": enabled_target_languages(app)}
            tmp = path.with_suffix(f"{path.suffix}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logging.warning("Could not write the language targets to %s", path, exc_info=True)

    # -- keys -------------------------------------------------------------------
    def _slot(self, provider: str, position: int) -> ApiKey | None:
        rows = self.repo.api_keys(provider)
        return rows[position] if position < len(rows) else None

    def _set_slot(self, field: str, value: str) -> None:
        if not value or value == SECRET_MASK:
            return
        provider, position = KEY_SLOTS[field]
        row = self._slot(provider, position)
        if row is None:
            self.repo.api_key_add(provider, self._label(provider, position), value)
        elif row.value != value:
            updated = self.repo.api_key_update(row.id, value=value)
            self._event("key.replaced", f"The {row.label} key of {provider} was replaced", provider=provider, key_id=row.id)
            if updated is not None and updated.status != row.status:  # a new key has not been refused yet
                self._event("key.status", f"The {row.label} key of {provider} is now {updated.status}",
                            provider=provider, key_id=row.id, status=updated.status)

    def _register_secrets(self) -> None:
        """Every stored key (and any key still in the environment) is hidden from the log and the event records."""
        values = [key.value for key in self.repo.api_keys()]
        for names in SEED_ENV.values():
            values.extend(str(self.env.get(name) or "") for name in names)
        DEFAULT_REDACTOR.add_values(values)

    def list_keys(self) -> dict[str, Any]:
        return {"items": [key_view(key) for key in self.repo.api_keys()], "warnings": self.warnings()}

    def usable_keys(self, provider: str) -> list[ApiKey]:
        """The keys a provider can be built from now: those not marked invalid or exhausted, oldest first."""
        return [key for key in self.repo.api_keys(provider) if key.status == KEY_OK]

    def add_key(self, body: Mapping[str, Any]) -> dict[str, Any]:
        provider = str(body.get("provider") or "").strip().lower()
        value = str(body.get("value") or "").strip()
        if provider not in KEY_PROVIDERS:
            raise ValueError(f"provider must be one of: {', '.join(KEY_PROVIDERS)}")
        if not value or value == SECRET_MASK:
            raise ValueError("value is required")
        label = str(body.get("label") or "").strip() or self._label(provider, len(self.repo.api_keys(provider)))
        with self._lock:
            key = self.repo.api_key_add(provider, label, value)
            self._register_secrets()
        self._event("key.added", f"A {provider} key was added", provider=provider, key_id=key.id)
        self._notify()
        return key_view(key)

    def update_key(self, key_id: int, body: Mapping[str, Any]) -> dict[str, Any] | None:
        status = body.get("status")
        if status is not None and status not in KEY_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(KEY_STATUSES)}")
        value = body.get("value")
        value = str(value).strip() if value is not None else None
        if value in ("", SECRET_MASK):
            value = None
        label = str(body["label"]).strip() if body.get("label") is not None else None
        with self._lock:
            before = next((key for key in self.repo.api_keys() if key.id == key_id), None)
            key = self.repo.api_key_update(key_id, label=label, value=value, status=status)
            self._register_secrets()
        if key is None or before is None:
            return None
        if key.status != before.status:
            self._event("key.status", f"The {key.label} key of {key.provider} is now {key.status}",
                        provider=key.provider, key_id=key.id, status=key.status)
        self._notify()
        return key_view(key)

    def remove_key(self, key_id: int) -> bool:
        with self._lock:
            key = next((item for item in self.repo.api_keys() if item.id == key_id), None)
            removed = self.repo.api_key_delete(key_id)
        if removed:
            self._event("key.removed", f"A {key.provider if key else ''} key was removed", key_id=key_id)
            self._notify()
        return removed

    def test_key(self, key_id: int, http: Any) -> dict[str, Any] | None:
        """"Thử khoá": ask the service whether the stored key works. A key that works becomes ``ok`` again (and the
        provider opens); one the service refuses becomes ``invalid``; an unreachable service changes nothing."""
        from .connection_tests import test_connection

        key = next((item for item in self.repo.api_keys() if item.id == key_id), None)
        if key is None:
            return None
        kind = {
            "bazarr": "bazarr", "tmdb": "tmdb_api_key", "gemini": "gemini_api_key",
            PROVIDER_OPENAI_COMPAT: "openai_compat_api_key",
        }.get(key.provider)
        if kind is None:
            return {"ok": False, "kind": key.provider, "message": f"No test exists for {key.provider} keys"}
        field = "bazarr_api_key" if kind == "bazarr" else kind
        current = self.settings()
        result = test_connection(
            kind, {"bazarr_url": current["bazarr_url"], "llm_base_url": current["llm_base_url"], field: key.value}, http
        )
        if result["ok"]:
            self.set_key_status(key.id, KEY_OK)
        elif result.get("auth_failed"):
            self.set_key_status(key.id, KEY_INVALID)
        current = next((item for item in self.repo.api_keys() if item.id == key_id), key)
        return {**result, "key": key_view(current)}

    def record_slot_test(self, field: str, result: Mapping[str, Any]) -> None:
        """A test of the key in one of the four console fields settles its status like "Test key" does."""
        provider, position = KEY_SLOTS[field]
        row = self._slot(provider, position)
        if row is None:
            return
        if result.get("ok"):
            self.set_key_status(row.id, KEY_OK)
        elif result.get("auth_failed"):
            self.set_key_status(row.id, KEY_INVALID)

    def set_key_status(self, key_id: int, status: str) -> None:
        if status not in KEY_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(KEY_STATUSES)}")
        self.update_key(key_id, {"status": status})

    def key_refused(self, key_id: int) -> None:
        """The provider answered an ``AuthError``: the key is marked invalid until someone replaces or retests it."""
        key = next((item for item in self.repo.api_keys() if item.id == key_id), None)
        if key is not None and key.status != KEY_INVALID:
            self.set_key_status(key_id, KEY_INVALID)

    def key_exhausted(self, key_id: int) -> None:
        key = next((item for item in self.repo.api_keys() if item.id == key_id), None)
        if key is not None and key.status == KEY_OK:
            self.set_key_status(key_id, KEY_EXHAUSTED)

    def key_used(self, key_id: int) -> None:
        """Remember the use of a key, at most once in half a minute."""
        now = self._clock()
        if now - self._touched.get(key_id, 0.0) < TOUCH_INTERVAL_SECONDS:
            return
        self._touched[key_id] = now
        try:
            self.repo.api_key_touch(key_id, now)
        except Exception:  # noqa: BLE001 - a record of use must never fail a call
            logging.debug("Could not record the use of key %s", key_id, exc_info=True)

    def model_not_found(self, model: str, message: str) -> None:
        """The provider does not know ``model``: a configuration error the Settings page shows (FR-17.5)."""
        if self.model_warnings.get(model) == message:
            return
        self.model_warnings[model] = message
        self._event("model.not_found", f"The provider does not know the model {model}; check the model setting",
                    level="WARNING", model=model)

    def model_found(self, model: str) -> None:
        self.model_warnings.pop(model, None)

    def warnings(self) -> list[dict[str, Any]]:
        """What the Settings page must warn about: invalid keys and models the provider does not know."""
        warnings: list[dict[str, Any]] = []
        for key in self.repo.api_keys():
            if key.status == KEY_INVALID:
                warnings.append({
                    "kind": "key-invalid", "provider": key.provider, "key_id": key.id, "label": key.label,
                    "message": f"The {key.label} key of {key.provider} was refused. Paste a new key, or test it again.",
                })
        runtime = self.settings()
        if runtime["llm_provider"] == PROVIDER_OPENAI_COMPAT and not runtime["llm_base_url"]:
            warnings.append({
                "kind": "provider-unconfigured", "provider": PROVIDER_OPENAI_COMPAT,
                "message": "The provider is openai_compat but no base URL is set (llm_base_url).",
            })
        settings = self.app_config()
        wanted = {
            str(settings.get(name) or "")
            for name in ("gst_model", "gst_fallback_model", "memory_model", "summarize_model", "research_model", "judge_model")
        }
        for model in sorted(self.model_warnings):
            if model in wanted:  # a model that is no longer configured has nothing left to warn about
                warnings.append({
                    "kind": "model-not-found", "model": model,
                    "message": f"The provider does not know the model {model}. Pick another in Settings.",
                })
        return warnings

    # -- change notification ----------------------------------------------------
    def subscribe(self, callback: Callable[[], None]) -> None:
        """``callback`` runs after the settings or the keys change (outside the service's lock)."""
        self._subscribers.append(callback)

    def _notify(self) -> None:
        for callback in list(self._subscribers):
            try:
                callback()
            except Exception:  # noqa: BLE001 - a subscriber must not undo a save
                logging.exception("A settings subscriber failed")

    def _event(self, kind: str, reason: str, *, level: str = "INFO", **data: Any) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.event(kind, level=level, reason=reason, **data)
        except Exception:  # noqa: BLE001 - best effort (NFR-11)
            pass
