"""Condense the summaries of old episodes into one arc summary (FR-5.8).

The only model call in the memory fold. The worker caches the result by ``(series, up_to_unit,
hash(entries))`` so the same input never costs a second call (NFR-3).
"""

from __future__ import annotations

from typing import Sequence

from ..providers.base import BadOutput, LLMRequest, Provider

__all__ = ["compress_arc"]

_SYSTEM = """\
You condense the plot summaries of several earlier episodes into one running summary of the story so
far. Keep: who the main characters are, how their relationships began and changed (romance, family,
friendship, rivalry, enmity, authority), and the events that still matter. Drop minor detail.
Write plain prose in English, at most {words} words, no headings and no lists. Write names as given."""


def compress_arc(provider: Provider, entries: Sequence[tuple[str, str]], *, max_words: int = 250) -> str:
    """One model call that merges ``(label, summary)`` entries; raises provider errors unchanged."""
    body = "\n".join(f"{label}: {text}" for label, text in entries)
    req = LLMRequest(
        system=_SYSTEM.format(words=max_words),
        user=f"EPISODE SUMMARIES, oldest first:\n{body}",
        response_schema=None,
        task="summarize",
        meta={"kind": "summarize", "entries": [label for label, _ in entries]},
    )
    text = provider.generate(req).text.strip()
    if not text:
        raise BadOutput("empty summary")
    return " ".join(text.split())
