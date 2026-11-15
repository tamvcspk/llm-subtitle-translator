"""Review a finished translation against its source (FR-21.3 ``validate``, FR-19.4).

One function for the CLI and the worker, so the same file gives the same findings everywhere (criterion 18f).
Pure: it reads the two cue lists and the glossary, nothing else.

Findings are either ``error`` (the output is not safe to publish as it is) or ``warning`` (a reviewer should
look). Kinds: ``count``, ``missing``, ``empty``, ``tags``, ``glossary`` (errors); ``lines``, ``line_length``,
``cps``, ``untranslated`` (warnings).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .. import srt
from ..glossary import Glossary
from .validate import check_cue

__all__ = ["Finding", "review", "CPS_LIMIT", "LINE_LIMIT"]

CPS_LIMIT = 20.0  # characters per second a viewer can read comfortably
LINE_LIMIT = 42  # characters per line, the common subtitle convention

_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


@dataclass(frozen=True)
class Finding:
    cue: int  # 1-based ordinal; 0 for the file as a whole
    kind: str
    severity: str  # error | warning
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _cps(text: str, duration_ms: int) -> float | None:
    if duration_ms <= 0:
        return None
    chars = len(text.replace("\n", ""))
    return chars / (duration_ms / 1000)


def review(source: list[srt.Cue], target: list[srt.Cue], glossary: Glossary | None = None) -> list[Finding]:
    findings: list[Finding] = []
    if len(source) != len(target):
        findings.append(Finding(0, "count", "error", f"{len(target)} cues, the source has {len(source)}"))
    for n, (src, out) in enumerate(zip(source, target), start=1):
        verdict = check_cue(src.text, out.text, glossary)
        if verdict.errors:
            kind = next(k for k in verdict.kinds if k in ("missing", "empty", "tags"))
            findings.append(Finding(n, kind, "error", "; ".join(verdict.errors)))
        if verdict.terms:
            findings.append(Finding(n, "glossary", "error", "; ".join(
                f'locked term "{t.source}" must be translated as "{t.target}"' for t in verdict.terms)))
        for warning in verdict.warnings:
            findings.append(Finding(n, "lines", "warning", warning))
        for line in out.text.split("\n"):
            if len(line) > LINE_LIMIT:
                findings.append(Finding(n, "line_length", "warning", f"{len(line)} characters on one line"))
                break
        cps = _cps(out.text, out.end_ms - out.start_ms)
        if cps is not None and cps > CPS_LIMIT and out.text.strip():
            findings.append(Finding(n, "cps", "warning", f"{cps:.1f} characters per second"))
        if (
            src.text.strip()
            and out.text.strip().casefold() == src.text.strip().casefold()
            and len(_LETTERS.findall(src.text)) >= 4
        ):
            findings.append(Finding(n, "untranslated", "warning", "the translation is the source text"))
    return findings
