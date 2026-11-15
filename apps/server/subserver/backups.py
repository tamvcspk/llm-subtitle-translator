from __future__ import annotations

import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any


BACKUP_RETENTION_DAYS = 30
SCHEDULED_BACKUP_INTERVAL_DAYS = 7
SECONDS_PER_DAY = 86400
SUPPORTED_BACKUP_APPS = {"gemini-srt-translator-bazarr", "bazarr-gemini-srt-worker"}


def backup_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "created_at": int(stat.st_mtime),
    }


def list_backups(state_dir: str, limit: int = 50) -> list[dict[str, Any]]:
    purge_old_backups(state_dir)
    backup_dir = Path(state_dir) / "backups"
    if not backup_dir.exists():
        return []
    files = sorted(backup_dir.glob("*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    return [backup_info(path) for path in files[:limit]]


def backup_file_path(state_dir: str, name: str) -> Path:
    clean_name = str(name or "").strip()
    if not clean_name or "/" in clean_name or "\\" in clean_name or Path(clean_name).name != clean_name:
        raise ValueError("invalid backup name")
    backup_dir = (Path(state_dir) / "backups").resolve()
    path = (backup_dir / clean_name).resolve()
    if path.parent != backup_dir or path.suffix.lower() != ".zip" or not path.is_file():
        raise ValueError("backup not found")
    return path


def purge_old_backups(state_dir: str, retention_days: int = BACKUP_RETENTION_DAYS, now: float | None = None) -> list[str]:
    backup_dir = Path(state_dir) / "backups"
    if not backup_dir.exists():
        return []
    cutoff = (time.time() if now is None else now) - retention_days * SECONDS_PER_DAY
    deleted: list[str] = []
    for path in sorted(backup_dir.glob("*.zip")):
        if path.stat().st_mtime >= cutoff:
            continue
        deleted.append(str(path))
        path.unlink()
    return deleted


def newest_backup_mtime(state_dir: str, reason: str | None = None) -> float | None:
    backup_dir = Path(state_dir) / "backups"
    if not backup_dir.exists():
        return None
    pattern = f"gemini-srt-translator-bazarr-{reason}-*.zip" if reason else "*.zip"
    mtimes = [path.stat().st_mtime for path in backup_dir.glob(pattern)]
    return max(mtimes) if mtimes else None


CONFIG_MEMBER = "config/config.json"
KEYS_MEMBER = "config/api_keys.json"
TARGETS_MEMBER = "postprocess/targets.json"


def create_backup(
    state_dir: str,
    config_path: str,
    postprocess_targets_path: str,
    reason: str = "manual",
    now: float | None = None,
    *,
    settings: Any = None,
    include_keys: bool = False,
) -> dict[str, Any]:
    """Write a backup zip. With ``settings`` (a ``SettingsService``) the configuration comes from the database and
    holds no API key unless ``include_keys`` asks for them (FR-20.3); the console account is never included.
    Without it the file at ``config_path`` is copied as it is (the layout before P6)."""
    purge_old_backups(state_dir, now=now)
    backup_dir = Path(state_dir) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    created_at = int(time.time() if now is None else now)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(created_at))
    backup_path = backup_dir / f"gemini-srt-translator-bazarr-{reason}-{stamp}.zip"
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"gemini-srt-translator-bazarr-{reason}-{stamp}-{counter}.zip"
        counter += 1

    with_keys = settings is not None and include_keys
    metadata = {
        "app": "gemini-srt-translator-bazarr",
        "reason": reason,
        "created_at": created_at,
        "contents": [CONFIG_MEMBER, *([KEYS_MEMBER] if with_keys else []), TARGETS_MEMBER],
        "includes_api_keys": with_keys,
    }
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if settings is not None:
            archive.writestr(CONFIG_MEMBER, json.dumps(settings.export_config(), ensure_ascii=False, indent=2))
            if with_keys:
                archive.writestr(KEYS_MEMBER, json.dumps(settings.export_keys(), ensure_ascii=False, indent=2))
        else:
            config = Path(config_path)
            if config.exists():
                archive.write(config, CONFIG_MEMBER)
        targets = Path(postprocess_targets_path)
        if targets.exists():
            archive.write(targets, TARGETS_MEMBER)
        archive.writestr("backup.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    os_time = (created_at, created_at)
    try:
        os.utime(backup_path, os_time)
    except OSError:
        pass
    return backup_info(backup_path)


def _read_json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        raw = archive.read(name)
    except KeyError as exc:
        raise ValueError(f"backup is missing {name}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"backup member {name} is not valid JSON") from exc


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.importing")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def restore_backup_archive(
    archive_bytes: bytes,
    state_dir: str,
    config_path: str,
    postprocess_targets_path: str,
    now: float | None = None,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    """Restore a backup zip. With ``settings`` the configuration (and the keys, when the backup holds them) go
    into the database; the console account is never restored (ADR-7). Without it ``config_path`` is rewritten."""
    keys: list[Any] | None = None
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            metadata = _read_json_member(archive, "backup.json")
            app_name = str(metadata.get("app") or "")
            if app_name and app_name not in SUPPORTED_BACKUP_APPS:
                raise ValueError("backup was created by a different app")
            config = _read_json_member(archive, CONFIG_MEMBER)
            targets = _read_json_member(archive, TARGETS_MEMBER)
            if KEYS_MEMBER in archive.namelist():
                keys = json.loads(archive.read(KEYS_MEMBER).decode("utf-8"))
                if not isinstance(keys, list):
                    raise ValueError(f"backup member {KEYS_MEMBER} is not a list of keys")
    except zipfile.BadZipFile as exc:
        raise ValueError("backup is not a valid zip file") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"backup member {KEYS_MEMBER} is not valid JSON") from exc

    pre_import_backup = create_backup(
        state_dir,
        config_path,
        postprocess_targets_path,
        reason="pre-import",
        now=now,
        settings=settings,
    )
    imported = [CONFIG_MEMBER, TARGETS_MEMBER]
    if settings is not None:
        settings.import_config(config, keys)
        if keys is not None:
            imported.insert(1, KEYS_MEMBER)
    else:
        _atomic_write_json(Path(config_path), config)
    _atomic_write_json(Path(postprocess_targets_path), targets)
    return {
        "imported": imported,
        "pre_import_backup": pre_import_backup,
        "metadata": metadata,
    }


def create_scheduled_backup_if_due(
    state_dir: str,
    config_path: str,
    postprocess_targets_path: str,
    interval_days: int = SCHEDULED_BACKUP_INTERVAL_DAYS,
    retention_days: int = BACKUP_RETENTION_DAYS,
    now: float | None = None,
    *,
    settings: Any = None,
) -> dict[str, Any] | None:
    current_time = time.time() if now is None else now
    purge_old_backups(state_dir, retention_days=retention_days, now=current_time)
    newest = newest_backup_mtime(state_dir, reason="scheduled")
    if newest is not None and current_time - newest < interval_days * SECONDS_PER_DAY:
        return None
    return create_backup(
        state_dir, config_path, postprocess_targets_path, reason="scheduled", now=current_time, settings=settings,
    )


class BackupMaintenance:
    """Narrow interface for backup retention and scheduled creation. A scheduled backup never holds API keys."""
    def __init__(self, state_dir: str, config_path: str, postprocess_targets_path: str, settings: Any = None) -> None:
        self.state_dir, self.config_path, self.postprocess_targets_path = state_dir, config_path, postprocess_targets_path
        self.settings = settings
    def run_if_due(self) -> dict[str, Any] | None:
        return create_scheduled_backup_if_due(
            self.state_dir, self.config_path, self.postprocess_targets_path, settings=self.settings
        )
    def create(self, reason: str = "manual") -> dict[str, Any]:
        return create_backup(
            self.state_dir, self.config_path, self.postprocess_targets_path, reason=reason, settings=self.settings
        )
