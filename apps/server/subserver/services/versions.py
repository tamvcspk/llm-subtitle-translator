"""Publishing target subtitles with version history, backup, revert, candidates and removal (FR-8.1, FR-16,
NFR-4, SPEC criteria 8 and 15).

A target file is *system-made* when the store has a ``current`` version for its path whose sha256 equals the file on
disk. Any other existing file (hand-edited, or put there by someone else) is never overwritten: it is first kept as
an ``external`` version. An automatic job never overwrites a file at all; a result that cannot go on disk safely
(the file appeared while the job ran, the base version moved, the source changed) is kept as a ``candidate`` for the
admin to adopt (FR-16.3, FR-16.4).

Publishing is announced before the rename (FR-16.5): the new version is written as ``publishing`` in the store, the
file is replaced, then the announcement is confirmed. ``reconcile`` (run at start-up) settles whatever a crash left.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..domain.ports import CANDIDATE, CURRENT, PRUNED, REMOVED, SUPERSEDED, Reclaimed, Telemetry, TranslationVersion
from ..adapters.store import Store
from ..adapters.telemetry import NULL as NULL_TELEMETRY

SKIPPED_EXISTING = "skipped-existing-output"
AUTO = "auto"
RETRANSLATE = "retranslate"
REVERT = "revert"
VARIANT = "variant"
EXTERNAL = "external"
CUE_EDIT = "cue_edit"
REMOVE = "remove"
DEFAULT_COALESCE_SECONDS = 600.0  # edit_coalesce_minutes = 10 (FR-16.7)


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
    status: str  # translated | candidate | skipped-existing-output | reverted | adopted | removed
    version: int | None = None
    reason: str = ""


_UNSET: Any = object()


@dataclass(frozen=True)
class VersionPage:
    """The current version and the newest status of each file of a page, read once. A file missing from ``current``
    has no current version; one missing from ``latest`` has no history."""

    current: dict[str, TranslationVersion]
    latest: dict[str, str]


class TranslationVersions:
    _HASHED_LIMIT = 4096  # files remembered by ``is_system_file(trusting_stat=True)``

    def __init__(
        self,
        repo: Store,
        backup_dir: str | Path,
        telemetry: Telemetry | None = None,
        *,
        coalesce_seconds: float = DEFAULT_COALESCE_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repo = repo  # the application's own SQLite store (ADR-12); callers outside read versions through it
        self.backup_dir = Path(backup_dir)
        self.telemetry: Telemetry = telemetry or NULL_TELEMETRY
        self.coalesce_seconds = coalesce_seconds
        self.clock = clock
        self._hashed: dict[str, tuple[tuple[int, int], str]] = {}

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

    @staticmethod
    def _write_file(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.publish.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    def _read_copy(self, version: TranslationVersion) -> bytes:
        if version.status == PRUNED or not version.stored_path:
            raise ValueError(
                f"version {version.version} was pruned by the retention settings (FR-13.3): its stored copy is gone, "
                "so it cannot be restored or compared"
            )
        try:
            data = Path(version.stored_path).read_bytes()
        except OSError:
            raise ValueError(f"the stored copy of version {version.version} is missing") from None
        if not hmac.compare_digest(sha256_bytes(data), version.sha256):
            raise ValueError(f"stored copy of version {version.version} is damaged")
        return data

    # -- state -------------------------------------------------------------
    def is_system_file(self, output_path: str, *, current: TranslationVersion | None | object = _UNSET,
                       trusting_stat: bool = False) -> bool:
        """Is the file on disk the stored current version, byte for byte?

        ``current`` is the version a caller already read. ``trusting_stat`` is for display only (a page of the
        Library): a file whose size and modification time are what they were when it was last hashed is taken to be
        unchanged, so it is not read again. Anything that decides what may be overwritten leaves it off."""
        path = Path(output_path)
        if current is _UNSET:
            current = self.repo.current_version(output_path)
        if not current or not path.is_file():
            return False
        if not trusting_stat:
            return hmac.compare_digest(sha256_file(path), current.sha256)
        try:
            stat = path.stat()
        except OSError:
            return False
        mark = (stat.st_mtime_ns, stat.st_size)
        known = self._hashed.get(output_path)
        if known is None or known[0] != mark:
            if len(self._hashed) >= self._HASHED_LIMIT:
                self._hashed.clear()
            known = (mark, sha256_file(path))
            self._hashed[output_path] = known
        return hmac.compare_digest(known[1], current.sha256)

    def latest_status(self, output_path: str) -> str:
        """Status of the newest version of a file ('' when there is none): a removed one stays removed (FR-16.6)."""
        return self.repo.latest_version_status(output_path)

    def page(self, output_paths: Iterable[str]) -> "VersionPage":
        """What a page of the Library needs of the history of these files, read in a few queries (R10-9)."""
        paths = [p for p in output_paths if p]
        return VersionPage(self.repo.current_versions_for(paths), self.repo.latest_version_statuses(paths))

    def _note_external(self, output_path: str) -> TranslationVersion | None:
        """Keep a file that is not the stored current version as an ``external`` version, once per content.

        Returns the version recorded, or None when the file is the system's own or already recorded."""
        path = Path(output_path)
        if not path.is_file() or self.is_system_file(output_path):
            return None
        data = path.read_bytes()
        digest = sha256_bytes(data)
        known = next((r for r in self.repo.versions(output_path) if r.sha256 == digest and r.status != PRUNED), None)
        if known is not None:
            return None
        version = self.repo.next_version(output_path)
        stored = self._write_copy(output_path, version, data)
        self.repo.add_version(
            output_path, version=version, stored_path=str(stored), sha256=digest, trigger=EXTERNAL,
            origin="external",
        )
        self.telemetry.event(
            "version.external", level="WARNING", target=path.name, version=version,
            reason="the target file was not the stored current version (edited or put there from outside); kept as a version before it was replaced",
        )
        return self.repo.get_version(output_path, version)

    # -- cache (NFR-3) -----------------------------------------------------
    def cached_text(self, source_sha256: str, target_lang: str, context_hash: str) -> str | None:
        """Text of an earlier result for the same input and context, if its stored copy is intact."""
        for row in self.repo.cached_versions(source_sha256, target_lang, context_hash):
            stored = Path(row.stored_path)
            try:
                data = stored.read_bytes()
            except OSError:
                continue
            if hmac.compare_digest(sha256_bytes(data), row.sha256):
                return data.decode("utf-8")
        return None

    # -- publishing ---------------------------------------------------------
    def _publish_file(self, output_path: str, version: int, data: bytes, **fields: Any) -> None:
        """The publish protocol (FR-16.5): copy, announce, rename, confirm."""
        stored = self._write_copy(output_path, version, data)
        self.repo.add_version(
            output_path, version=version, stored_path=str(stored), sha256=sha256_bytes(data),
            status=CURRENT, publishing=True, **fields,
        )
        self._write_file(Path(output_path), data)
        self.repo.set_version(output_path, version, publishing=False)

    def _candidate(self, output_path: str, data: bytes, reason: str, **fields: Any) -> Published:
        """Keep a result that must not go on disk now (FR-16.3). Nothing is written to the target."""
        current = self.repo.current_version(output_path)
        version = self.repo.next_version(output_path)
        stored = self._write_copy(output_path, version, data)
        self.repo.add_version(
            output_path, version=version, stored_path=str(stored), sha256=sha256_bytes(data),
            status=CANDIDATE, parent_version=current.version if current else None, **fields,
        )
        self.telemetry.event(
            "version.candidate", job_id=fields.get("job_id", ""), unit_id=fields.get("unit_id"),
            target=Path(output_path).name, version=version, reason=reason,
        )
        return Published("candidate", version, reason)

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
        memory_hash: str = "",
        context_complete: bool = True,
        missing_units: Iterable[str] = (),
        job_id: str = "",
        base_version: int | None = None,
        candidate_reason: str = "",
        params: dict[str, Any] | None = None,
    ) -> Published:
        """Put a result into the file's history.

        ``base_version`` is the current version when the job started (a replace job, FR-16.3); ``candidate_reason``
        is set by the caller when the source changed meanwhile (FR-15.10). Either one, or a file that appeared while
        an automatic job ran, makes the result a candidate instead of overwriting anything. A ``variant`` (FR-18.8) is
        always a candidate: it is made to be compared, never to be put on disk by a job. ``params`` says how the
        version was made (a retranslated range, a variant's style) when its other fields do not.
        """
        path = Path(output_path)
        data = text.encode("utf-8")
        meta: dict[str, Any] = dict(
            trigger=trigger, source_path=source_path, source_sha256=source_sha256, target_lang=target_lang,
            context_hash=context_hash, model=model, tokens_in=tokens_in, tokens_out=tokens_out, unit_id=unit_id,
            glossary_hash=glossary_hash, memory_hash=memory_hash, context_complete=context_complete,
            missing_units=tuple(missing_units), job_id=job_id, origin=_origin(trigger), params=params,
        )

        if trigger == VARIANT:
            return self._candidate(output_path, data, "a variant translation: kept to compare, never put on disk by a job", **meta)

        if trigger == AUTO and path.is_file():
            if self.is_system_file(output_path):
                return Published(SKIPPED_EXISTING)
            self._note_external(output_path)  # a person or Bazarr put a file here while the job ran
            return self._candidate(
                output_path, data, "a target subtitle appeared while the job ran; the result waits as a candidate",
                **meta,
            )

        current = self.repo.current_version(output_path)
        if candidate_reason or (
            trigger == RETRANSLATE and base_version is not None
            and base_version != (current.version if current else None)
        ):
            self._note_external(output_path)
            reason = candidate_reason or (
                f"the subtitle changed while the job ran (it started from version {base_version}); "
                "the result waits as a candidate"
            )
            return self._candidate(output_path, data, reason, **meta)

        if path.is_file() and trigger != AUTO:
            self._note_external(output_path)  # a hand edit is kept before anything replaces it (FR-16.2)

        current = self.repo.current_version(output_path)
        if trigger == CUE_EDIT and current and current.origin == "edit" and self._within_coalesce(current):
            # Consecutive edits in one sitting are one version (FR-16.7): the version's copy takes the new text.
            stored = self._write_copy(output_path, current.version, data)
            self.repo.set_version(
                output_path, current.version, stored_path=str(stored), sha256=sha256_bytes(data),
                edited_at=self.clock(), publishing=True,
            )
            self._write_file(path, data)
            self.repo.set_version(output_path, current.version, publishing=False)
            self._event_published(
                path, current.version, trigger, unit_id, job_id, model, data, current.parent_version, coalesced=True,
            )
            return Published("translated", current.version)

        version = self.repo.next_version(output_path)
        self._publish_file(
            output_path, version, data,
            parent_version=current.version if current else None,
            edited_at=self.clock() if trigger == CUE_EDIT else 0.0,
            **meta,
        )
        self._event_published(path, version, trigger, unit_id, job_id, model, data, current.version if current else None)
        return Published("translated", version)

    def _within_coalesce(self, current: TranslationVersion) -> bool:
        return bool(current.edited_at) and self.clock() - current.edited_at <= self.coalesce_seconds

    def _event_published(
        self, path: Path, version: int, trigger: str, unit_id: int | None, job_id: str, model: str, data: bytes,
        parent: int | None, *, coalesced: bool = False,
    ) -> None:
        if coalesced:
            reason = f"cue edits made within {int(self.coalesce_seconds)} s of each other are one version ({version})"
            kind = "version.coalesced"
        else:
            reason = f"version {version} published ({trigger})"
            kind = "version.published"
        self.telemetry.event(
            kind, job_id=job_id, unit_id=unit_id, target=path.name, version=version, trigger=trigger, model=model,
            parent=parent, sha256=sha256_bytes(data), reason=reason,
        )

    # -- revert, adopt, remove -------------------------------------------------
    def revert(self, output_path: str, version: int) -> Published:
        """Restore an earlier version byte for byte; the restore is itself a new version."""
        target = self.repo.get_version(output_path, version)
        if target is None:
            raise ValueError(f"no version {version} for {output_path}")
        if target.status == CANDIDATE:
            raise ValueError(f"version {version} is a candidate and was never on disk; adopt it instead")
        data = self._read_copy(target)
        self._note_external(output_path)
        new_version = self.repo.next_version(output_path)
        current = self.repo.current_version(output_path)
        self._publish_file(
            output_path, new_version, data,
            trigger=REVERT, origin="revert", source_path=target.source_path, source_sha256=target.source_sha256,
            target_lang=target.target_lang, context_hash=target.context_hash, parent_version=version,
            model=target.model, unit_id=target.unit_id, glossary_hash=target.glossary_hash,
            memory_hash=target.memory_hash, context_complete=target.context_complete, missing_units=target.missing_units,
        )
        self.telemetry.event(
            "version.reverted", unit_id=target.unit_id, target=Path(output_path).name, version=new_version,
            restored=version, sha256=target.sha256, reason=f"restored version {version} as version {new_version}",
            replaced=current.version if current else None,
        )
        return Published("reverted", new_version)

    def adopt(self, output_path: str, version: int) -> Published:
        """"Dùng bản này" (FR-16.4): a candidate becomes the current version and goes on disk."""
        target = self.repo.get_version(output_path, version)
        if target is None:
            raise LookupError(f"no version {version}")
        if target.status != CANDIDATE:
            raise ValueError(f"version {version} is {target.status}, not a candidate")
        data = self._read_copy(target)
        self._note_external(output_path)  # a hand edit made meanwhile stays in the history
        self.repo.set_version(output_path, version, status=CURRENT, publishing=True)
        self._write_file(Path(output_path), data)
        self.repo.set_version(output_path, version, publishing=False)
        self.telemetry.event(
            "version.adopted", unit_id=target.unit_id, target=Path(output_path).name, version=version,
            reason=f"candidate {version} adopted; it is now the file's current version",
        )
        return Published("adopted", version)

    def remove(self, output_path: str, *, reason: str = "removed from the console") -> Published:
        """The admin deletes the target subtitle (FR-16.6): a ``removed`` tombstone keeps the content for revert."""
        current = self.repo.current_version(output_path)
        if current is None:
            raise LookupError("there is no current version of this subtitle to remove")
        path = Path(output_path)
        self._note_external(output_path)
        current = self.repo.current_version(output_path)
        if current is None:
            raise LookupError("there is no current version of this subtitle to remove")
        try:
            data = self._read_copy(current)
        except ValueError:
            data = path.read_bytes() if path.is_file() else b""
        version = self.repo.next_version(output_path)
        stored = self._write_copy(output_path, version, data)
        self.repo.add_version(
            output_path, version=version, stored_path=str(stored), sha256=sha256_bytes(data), trigger=REMOVE,
            status=REMOVED, publishing=True, origin="edit", parent_version=current.version,
            unit_id=current.unit_id, target_lang=current.target_lang,
        )
        path.unlink(missing_ok=True)
        self.repo.set_version(output_path, version, publishing=False)
        self.telemetry.event(
            "version.removed", unit_id=current.unit_id, target=path.name, version=version, reason=reason,
        )
        return Published("removed", version, reason)

    # -- start-up (FR-16.5) -------------------------------------------------------
    def reconcile(self) -> dict[str, int]:
        """Settle what a crash left behind, then note target files that vanished outside the console (FR-16.6).

        A publish whose file already carries its sha256 is confirmed; one whose file does not is cancelled: the
        result becomes a candidate and the version that is on disk is put back in force, or the file is kept as an
        external version when it matches none. Nothing is lost either way."""
        counts = {"confirmed": 0, "cancelled": 0, "removed": 0, "vanished": 0}
        for row in self.repo.publishing_versions():
            path = Path(row.output_path)
            if row.status == REMOVED:
                path.unlink(missing_ok=True)
                self.repo.set_version(row.output_path, row.version, publishing=False)
                counts["removed"] += 1
                continue
            on_disk = sha256_file(path) if path.is_file() else None
            if on_disk == row.sha256:
                self.repo.set_version(row.output_path, row.version, publishing=False)
                counts["confirmed"] += 1
                continue
            logging.warning("Publish of version %s of %s did not finish; cancelled at start-up", row.version, path.name)
            self.repo.set_version(row.output_path, row.version, status=CANDIDATE, publishing=False)
            previous = max(
                (r for r in self.repo.versions(row.output_path)
                 if r.status == SUPERSEDED and r.version < row.version),
                key=lambda r: r.version, default=None,
            )
            if previous is not None and on_disk == previous.sha256:
                self.repo.set_version(row.output_path, previous.version, status=CURRENT)
            elif on_disk is not None:
                self._note_external(row.output_path)
            self.telemetry.event(
                "version.publish_cancelled", level="WARNING", unit_id=row.unit_id, target=path.name,
                version=row.version, reason="the file did not carry the announced content after a restart; the result is a candidate",
            )
            counts["cancelled"] += 1
        for current in self.repo.current_versions():
            target = Path(current.output_path)
            # A file is only "removed outside" when its folder is there: a media share that is not mounted yet
            # must not turn the whole library into removals that no automatic job would ever undo.
            if target.is_file() or not target.parent.is_dir():
                continue
            data = self._read_copy_or_none(current)
            if data is None:
                continue
            version = self.repo.next_version(current.output_path)
            stored = self._write_copy(current.output_path, version, data)
            self.repo.add_version(
                current.output_path, version=version, stored_path=str(stored), sha256=sha256_bytes(data),
                trigger=EXTERNAL, status=REMOVED, origin="external", unit_id=current.unit_id,
                target_lang=current.target_lang, parent_version=current.version,
            )
            self.telemetry.event(
                "version.external_removed", level="WARNING", unit_id=current.unit_id, target=Path(current.output_path).name,
                version=version, reason="the target file was removed outside the console; recorded as removed",
            )
            counts["vanished"] += 1
        return counts

    # -- retention (FR-13.1, FR-13.3) -------------------------------------------------
    def prune(self, keep: int, *, dry_run: bool = False) -> Reclaimed:
        """Delete the stored copies retention no longer needs and mark their versions ``pruned``.

        Each target file keeps its current version, its first version and the ``keep`` newest; nothing else of
        the history is touched (the lines stay, only the copies go). A copy is deleted only when it lies inside
        the backup folder: a row whose path points anywhere else (a media folder, say) is left alone. The copy goes
        first and the mark second, so a crash in between is finished by the next run.
        """
        root = self.backup_dir.resolve()
        files = size = 0
        for row in self.repo.prunable_versions(keep):
            stored = Path(row.stored_path)
            try:
                inside = stored.resolve().is_relative_to(root)
            except OSError:
                inside = False
            if not inside:
                continue
            try:
                length = stored.stat().st_size
            except OSError:
                length = None  # already gone: only the mark is missing
            if not dry_run:
                try:
                    stored.unlink(missing_ok=True)
                except OSError:
                    logging.warning("Retention could not delete %s; the version stays as it is", stored.name)
                    continue
                self.repo.set_version(row.output_path, row.version, status=PRUNED, stored_path="")
                self._remove_empty_folder(stored.parent, root)
            files += 1 if length is not None else 0
            size += length or 0
        return Reclaimed(files=files, bytes=size)

    @staticmethod
    def _remove_empty_folder(folder: Path, root: Path) -> None:
        try:
            if folder != root and folder.is_relative_to(root) and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass

    def _read_copy_or_none(self, version: TranslationVersion) -> bytes | None:
        try:
            return self._read_copy(version)
        except ValueError:
            return None


def _origin(trigger: str) -> str:
    return {CUE_EDIT: "edit", REMOVE: "edit", REVERT: "revert", EXTERNAL: "external"}.get(trigger, "machine")
