"""Glossaries in the database (FR-4, FR-18.10, ADR-14).

A glossary belongs to an owner (a series, or a movie) and a language, and is stored as a *document*: the YAML schema as
a mapping (``subcore.glossary.to_doc``). The database is the single source of truth; YAML is only the exchange format:
``read_text`` exports it, ``write`` imports it, and ``import_files`` reads the old ``series/<key>/glossary.<lang>.yaml``
files once, leaving them where they are. Each save keeps what it replaced in ``glossary_history`` (this is what the old
``backups/glossary/`` folder was for), and a save of the same document changes nothing.

A document is checked before it is stored, so a bad save can never leave a glossary that stops the next job; a stored
document that no longer reads (a hand-edited database) raises when it is loaded, so a translation never silently runs
without it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from subcore import glossary as g

from .ports import GlossaryRevision, Reclaimed, StoredGlossary
from .store import Store

SERIES = "series"
MOVIES = "movies"
IMPORTED_MARK = "_glossaries_imported"  # a settings row: the one-time import of the YAML files has run


def safe_name(value: str) -> str:
    """A key as a single, portable path component (how the YAML folders were named, and so how owners are)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "unnamed"
    return cleaned[:100]


def _clean_lang(lang: str) -> str:
    code = safe_name(lang.lower())
    if not code or code != lang.lower():
        raise ValueError(f"invalid language code {lang!r}")
    return code


def _kind(kind: str) -> str:
    if kind not in (SERIES, MOVIES):
        raise ValueError(f"a glossary belongs to a series or a movie, not {kind!r}")
    return kind


def doc_hash(doc: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ImportReport:
    imported: int = 0
    skipped: int = 0  # a glossary of that owner and language was already in the database
    broken: tuple[str, ...] = field(default=())  # files that did not read; they stay where they are


class Glossaries:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- reading -------------------------------------------------------------------
    def stored(self, kind: str, key: str, lang: str) -> StoredGlossary | None:
        return self.store.glossary(_kind(kind), safe_name(key), _clean_lang(lang))

    def load(self, kind: str, key: str, lang: str) -> g.Glossary:
        """The parsed glossary; a stored document that is invalid raises so no translation silently runs without it."""
        stored = self.stored(kind, key, lang)
        if stored is None:
            return g.Glossary(target_lang=lang)
        try:
            return g.from_doc(stored.doc)
        except g.GlossaryError as exc:
            raise RuntimeError(f"Glossary {kind}/{key}/{lang} is invalid: {exc}") from None

    def read_text(self, kind: str, key: str, lang: str) -> str:
        """The YAML export; empty text when the owner has no glossary in that language."""
        stored = self.stored(kind, key, lang)
        return g.dumps(g.from_doc(stored.doc)) if stored else ""

    def languages(self, kind: str, key: str) -> list[str]:
        return self.store.glossary_languages(_kind(kind), safe_name(key))

    # -- writing ---------------------------------------------------------------------
    def save_doc(self, kind: str, key: str, lang: str, doc: dict[str, Any], *, note: str = "console") -> StoredGlossary:
        """Validate ``doc`` (raises :class:`subcore.glossary.GlossaryError` naming the offending path) and store it.
        The stored form is the normalised one, so a document and its YAML export always agree."""
        glossary = g.from_doc(doc)
        lang = _clean_lang(lang)
        if not glossary.target_lang:
            glossary = replace(glossary, target_lang=lang)
        clean = g.to_doc(glossary)
        return self.store.glossary_save(_kind(kind), safe_name(key), lang, clean, doc_hash(clean), note=note)

    def save(self, kind: str, key: str, lang: str, glossary: g.Glossary, *, note: str = "console") -> StoredGlossary:
        return self.save_doc(kind, key, lang, g.to_doc(glossary), note=note)

    def write(self, kind: str, key: str, lang: str, text: str, *, note: str = "import") -> StoredGlossary:
        """Import YAML text (the exchange format). An empty text is an empty glossary."""
        return self.save(kind, key, lang, g.loads(text) if text.strip() else g.Glossary(), note=note)

    def history(self, kind: str, key: str, lang: str, *, limit: int = 50) -> list[GlossaryRevision]:
        return self.store.glossary_history(_kind(kind), safe_name(key), _clean_lang(lang), limit=limit)

    def restore(self, kind: str, key: str, lang: str, version: int) -> StoredGlossary:
        """Put an earlier state back as a new save (the state it replaces goes to the history, so a restore can be undone)."""
        for revision in self.history(kind, key, lang, limit=1000):
            if revision.version == version:
                return self.save_doc(kind, key, lang, revision.doc, note="restore")
        raise LookupError(f"no earlier version {version} of this glossary")

    def copy(self, source: tuple[str, str], target: tuple[str, str], lang: str, *, merge: bool) -> StoredGlossary:
        """Copy the glossary of another series into this one (Non-goals: no link, a copy). ``merge`` keeps what the
        target already has and lets its own entries win on conflicts; otherwise the target is replaced."""
        copied = self.load(*source, lang)
        if copied.is_empty():
            raise LookupError("the other series has no glossary in this language")
        result = g.merge(copied, self.load(*target, lang)) if merge else copied
        return self.save(*target, lang, result, note="copy")

    def prune_history(self, keep: int, *, dry_run: bool = False) -> Reclaimed:
        """Keep the ``keep`` newest earlier states of each glossary (FR-13.1); the current glossary is never touched."""
        return self.store.glossary_prune_history(keep, dry_run=dry_run)

    # -- the one-time import of the YAML files ----------------------------------------------
    def import_files(self, data_dir: str | Path) -> ImportReport:
        """Read ``<data_dir>/{series,movies}/<key>/glossary.<lang>.yaml`` into the database, once. The files stay
        where they are. A glossary already in the database wins over its file, and a file that does not read is
        named in the report and left alone (it can still be imported by hand from the console)."""
        if any(row.key == IMPORTED_MARK for row in self.store.settings_rows()):
            return ImportReport()
        root = Path(data_dir)
        imported, skipped, broken = 0, 0, []
        for kind in (SERIES, MOVIES):
            for path in sorted((root / kind).glob("*/glossary.*.yaml")):
                lang = path.name[len("glossary."):-len(".yaml")]
                key = path.parent.name
                try:
                    if self.stored(kind, key, lang) is not None:
                        skipped += 1
                        continue
                    self.write(kind, key, lang, path.read_text(encoding="utf-8-sig"), note="import")
                    imported += 1
                except (g.GlossaryError, ValueError, OSError) as exc:
                    logging.warning("Glossary file %s was not imported: %s", path, exc)
                    broken.append(str(path))
        self.store.setting_save({IMPORTED_MARK: True})
        return ImportReport(imported, skipped, tuple(broken))
