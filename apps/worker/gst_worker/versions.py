"""Publishing target subtitles with version history, backup and revert (FR-8.1, NFR-4, SPEC criterion 8).

A target file is *system-made* when the store has a ``current`` version for its path whose sha256
equals the file on disk. Any other existing file (hand-edited, or put there by someone else) is
never overwritten by an automatic job; an explicit retranslate or revert first keeps it as an
``external`` version, so nothing is ever lost.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path

from subcore import srt

from .store import Store

SKIPPED_EXISTING = "skipped-existing-output"
AUTO = "auto"
RETRANSLATE = "retranslate"
REVERT = "revert"
EXTERNAL = "external"
CUE_EDIT = "cue_edit"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Published:
    status: str  # translated | skipped-existing-output | reverted
    version: int | None = None


class TranslationVersions:
    def __init__(self, store: Store, backup_dir: str | Path) -> None:
        self.store = store
        self.backup_dir = Path(backup_dir)

    # -- paths -------------------------------------------------------------
    def _version_path(self, output_path: str, version: int) -> Path:
        key = hashlib.sha1(output_path.encode("utf-8")).hexdigest()[:16]
        name = Path(output_path).name
        return self.backup_dir / key / f"v{version}-{name}"

    def _write_copy(self, output_path: str, version: int, data: bytes) -> Path:
        stored = self._version_path(output_path, version)
        stored.parent.mkdir(parents=True, exist_ok=True)
        temporary = stored.with_name(f".{stored.name}.tmp")
        temporary.write_bytes(data)
        temporary.replace(stored)
        return stored

    # -- state -------------------------------------------------------------
    def is_system_file(self, output_path: str) -> bool:
        path = Path(output_path)
        current = self.store.current_version(output_path)
        return bool(
            current
            and path.is_file()
            and hmac.compare_digest(sha256_file(path), str(current["sha256"]))
        )

    def _keep_external(self, output_path: str) -> None:
        """Back up an existing file that is not the current system version."""
        path = Path(output_path)
        if not path.is_file() or self.is_system_file(output_path):
            return
        data = path.read_bytes()
        version = self.store.next_version(output_path)
        stored = self._write_copy(output_path, version, data)
        self.store.add_version(
            output_path, version=version, stored_path=str(stored), sha256=sha256_bytes(data), trigger=EXTERNAL
        )

    # -- cache (NFR-3) -----------------------------------------------------
    def cached_text(self, source_sha256: str, target_lang: str, context_hash: str) -> str | None:
        """Text of an earlier result for the same input and context, if its stored copy is intact."""
        for row in self.store.cached_versions(source_sha256, target_lang, context_hash):
            stored = Path(str(row["stored_path"]))
            try:
                data = stored.read_bytes()
            except OSError:
                continue
            if hmac.compare_digest(sha256_bytes(data), str(row["sha256"])):
                return data.decode("utf-8")
        return None

    # -- publish / revert --------------------------------------------------
    def publish(
        self,
        output_path: str,
        text: str,
        *,
        trigger: str = AUTO,
        source_path: str = "",
        source_sha256: str = "",
        target_lang: str = "",
        context_hash: str = "",
        model: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        unit_id: int | None = None,
        glossary_hash: str = "",
    ) -> Published:
        path = Path(output_path)
        if path.exists() and trigger == AUTO:
            return Published(SKIPPED_EXISTING)
        if path.exists():
            self._keep_external(output_path)
        data = text.encode("utf-8")
        parent = self.store.current_version(output_path)
        version = self.store.next_version(output_path)
        stored = self._write_copy(output_path, version, data)
        if trigger == AUTO and path.exists():  # appeared while the copy was written
            return Published(SKIPPED_EXISTING)
        srt.write_atomic(path, text)
        self.store.add_version(
            output_path,
            version=version,
            stored_path=str(stored),
            sha256=sha256_bytes(data),
            trigger=trigger,
            source_path=source_path,
            source_sha256=source_sha256,
            target_lang=target_lang,
            context_hash=context_hash,
            parent_version=int(parent["version"]) if parent else None,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            unit_id=unit_id,
            glossary_hash=glossary_hash,
        )
        return Published("translated", version)

    def revert(self, output_path: str, version: int) -> Published:
        """Restore an earlier version byte for byte; the restore is itself a new version."""
        target = self.store.get_version(output_path, version)
        if target is None:
            raise ValueError(f"no version {version} for {output_path}")
        stored = Path(str(target["stored_path"]))
        data = stored.read_bytes()
        if not hmac.compare_digest(sha256_bytes(data), str(target["sha256"])):
            raise ValueError(f"stored copy of version {version} is damaged")
        self._keep_external(output_path)
        new_version = self.store.next_version(output_path)
        copy = self._write_copy(output_path, new_version, data)
        temporary = Path(output_path).with_name(f".{Path(output_path).name}.revert.tmp")
        temporary.write_bytes(data)
        temporary.replace(output_path)
        self.store.add_version(
            output_path,
            version=new_version,
            stored_path=str(copy),
            sha256=str(target["sha256"]),
            trigger=REVERT,
            source_path=str(target["source_path"]),
            source_sha256=str(target["source_sha256"]),
            target_lang=str(target["target_lang"]),
            context_hash=str(target["context_hash"]),
            parent_version=version,
            model=str(target["model"]),
            unit_id=target.get("unit_id"),
            glossary_hash=str(target.get("glossary_hash") or ""),
        )
        return Published("reverted", new_version)
