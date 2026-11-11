"""Parse and validate model replies (FR-3.2). Providers are never trusted."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import json_repair

from .. import srt
from ..glossary import Glossary, Term, locked_violations

__all__ = ["Reply", "Verdict", "parse_reply", "check_cue"]


@dataclass
class Reply:
    items: dict[int, str] = field(default_factory=dict)  # first occurrence per allowed id
    extras: list[int] = field(default_factory=list)  # ids that were not requested
    duplicates: list[int] = field(default_factory=list)
    unparseable: bool = False


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _find_list(data: Any) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "i" in data and "text" in data:
            return [data]
        for value in data.values():
            if isinstance(value, list):
                return value
    return None


def parse_reply(text: str, allowed: set[int]) -> Reply:
    """Extract ``{i, text}`` items; tolerate fences, trailing commas, truncation (json-repair)."""
    reply = Reply()
    try:
        data = json_repair.loads(text)
    except Exception:  # noqa: BLE001 - json_repair raises assorted errors on garbage
        data = None
    items = _find_list(data)
    if items is None:
        reply.unparseable = True
        return reply
    for entry in items:
        if not isinstance(entry, dict):
            continue
        ident = _as_int(entry.get("i", entry.get("index")))
        body = entry.get("text")
        if ident is None or not isinstance(body, str):
            continue
        if ident not in allowed:
            reply.extras.append(ident)
        elif ident in reply.items:
            reply.duplicates.append(ident)
        else:
            reply.items[ident] = body.strip()
    return reply


@dataclass
class Verdict:
    """Outcome for one cue. ``errors`` force a repair; ``terms`` are glossary violations."""

    errors: list[str] = field(default_factory=list)
    terms: list[Term] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.terms


def check_cue(source: str, translated: str | None, glossary: Glossary | None = None) -> Verdict:
    verdict = Verdict()
    if translated is None:
        verdict.errors.append("missing from your answer")
        return verdict
    if source.strip() and not translated.strip():
        verdict.errors.append("empty translation")
        return verdict
    verdict.errors.extend(srt.tag_problems(source, translated))
    warning = srt.line_count_warning(translated)
    if warning:
        verdict.warnings.append(warning)
    if glossary is not None:
        verdict.terms = locked_violations(glossary, source, translated)
    return verdict
