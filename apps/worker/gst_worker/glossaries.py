"""Glossary YAML files on disk: ``DATA_DIR/series/<key>/glossary.<lang>.yaml`` (FR-4.1, ADR-5).

The YAML is the single source of truth for series-level rules and stays hand-editable. Writes made
through the console validate first, keep the previous file under ``DATA_DIR/backups/glossary/`` and
replace the file atomically, so a bad save can never leave a half-written glossary. (Saving through
the console re-renders the file, which drops YAML comments; the backup keeps the original text.)
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from subcore import glossary as g

SERIES = "series"
MOVIES = "movies"


def safe_name(value: str) -> str:
    """A key as a single, portable path component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "unnamed"
    return cleaned[:100]


def _clean_lang(lang: str) -> str:
    code = safe_name(lang.lower())
    if not code or code != lang.lower():
        raise ValueError(f"invalid language code {lang!r}")
    return code


class GlossaryFiles:
    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir)

    def path(self, kind: str, key: str, lang: str) -> Path:
        return self.root / kind / safe_name(key) / f"glossary.{_clean_lang(lang)}.yaml"

    def read_text(self, kind: str, key: str, lang: str) -> str:
        try:
            return self.path(kind, key, lang).read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return ""

    def load(self, kind: str, key: str, lang: str) -> g.Glossary:
        """The parsed glossary; a broken file raises so no translation silently runs without it."""
        text = self.read_text(kind, key, lang)
        try:
            return g.loads(text) if text.strip() else g.Glossary(target_lang=lang)
        except g.GlossaryError as exc:
            raise RuntimeError(f"Glossary {kind}/{key}/{lang} is invalid: {exc}") from None

    def write(self, kind: str, key: str, lang: str, text: str) -> Path:
        """Validate ``text`` and replace the glossary atomically, keeping the previous file."""
        g.loads(text)  # raises GlossaryError (a ValueError) naming the offending path
        path = self.path(kind, key, lang)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            backup_dir = self.root / "backups" / "glossary" / kind / safe_name(key)
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            (backup_dir / f"{lang}-{stamp}.yaml").write_bytes(path.read_bytes())
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text if text.endswith("\n") else text + "\n")
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return path

    def save(self, kind: str, key: str, lang: str, glossary: g.Glossary) -> Path:
        if not glossary.target_lang:
            glossary = replace(glossary, target_lang=lang)
        return self.write(kind, key, lang, g.dumps(glossary))
