"""Side-by-side numbers for two translation configurations, or one configuration against a golden set (FR-21.3).

Pure: callers run the bundles and pass the results in. Similarity uses ``difflib`` so the same inputs always give
the same numbers, with no model judging the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Sequence

from .translate.engine import TranslationResult

__all__ = ["Summary", "summarize", "changed_cues", "format_table"]


@dataclass
class Summary:
    name: str
    cues: int
    calls: int
    tokens_in: int
    tokens_out: int
    warnings: int
    errors: int  # review findings with severity error
    review_warnings: int  # review findings with severity warning
    bundle_hash: str = ""
    golden_exact: int | None = None
    golden_similarity: float | None = None
    by_kind: dict[str, int] = field(default_factory=dict)


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def summarize(
    name: str,
    result: TranslationResult,
    findings: Sequence,
    *,
    bundle_hash: str = "",
    golden: Sequence[str] | None = None,
) -> Summary:
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    out = Summary(
        name=name,
        cues=len(result.cues),
        calls=len(result.calls),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        warnings=len(result.warnings),
        errors=sum(1 for f in findings if f.severity == "error"),
        review_warnings=sum(1 for f in findings if f.severity == "warning"),
        bundle_hash=bundle_hash,
        by_kind=by_kind,
    )
    if golden is not None:
        texts = [c.text for c in result.cues]
        pairs = list(zip(texts, golden))
        out.golden_exact = sum(1 for a, b in pairs if a.strip() == b.strip())
        out.golden_similarity = (
            sum(similarity(a, b) for a, b in pairs) / len(pairs) if pairs else None
        )
    return out


def changed_cues(a: TranslationResult, b: TranslationResult) -> int:
    """How many cues the two configurations write differently."""
    return sum(1 for x, y in zip(a.cues, b.cues) if x.text != y.text)


def format_table(summaries: Sequence[Summary], changed: int | None = None) -> str:
    rows: list[tuple[str, list[str]]] = [
        ("cues", [str(s.cues) for s in summaries]),
        ("model calls", [str(s.calls) for s in summaries]),
        ("tokens in / out", [f"{s.tokens_in} / {s.tokens_out}" for s in summaries]),
        ("warnings", [str(s.warnings) for s in summaries]),
        ("review errors", [str(s.errors) for s in summaries]),
        ("review warnings", [str(s.review_warnings) for s in summaries]),
    ]
    if any(s.golden_exact is not None for s in summaries):
        rows.append(("golden: exact cues", [
            "-" if s.golden_exact is None else str(s.golden_exact) for s in summaries
        ]))
        rows.append(("golden: similarity", [
            "-" if s.golden_similarity is None else f"{s.golden_similarity:.3f}" for s in summaries
        ]))
    if changed is not None:
        rows.append(("cues that differ", [str(changed)] + [""] * (len(summaries) - 1)))
    headers = ["", *[s.name for s in summaries]]
    widths = [max(len(headers[0]), *(len(label) for label, _ in rows))]
    for i in range(len(summaries)):
        widths.append(max(len(headers[i + 1]), *(len(values[i]) for _, values in rows)))
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    for label, values in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip([label, *values], widths)))
    return "\n".join(lines)
