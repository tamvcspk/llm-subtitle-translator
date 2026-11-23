"""Research grounding: how the web writes the names and terms of a story in the target language (FR-7; P16).

What to look up is decided without a model, by :func:`subcore.suggest.term_items` (a name or term that shows in two or more
episodes and is not in the glossary yet). This module only asks, in two calls, and never applies anything:

1. **Search** (task ``research``, the search tool on, no schema): "how is each of these written in subtitles of the
   language?". The provider answers in prose and reports the pages it used as grounding metadata (``LLMResponse.grounding_sources``,
   and ``cited_text``, the answer with a ``[n]`` after each passage a page backs).
2. **Read** (task ``research`` too, structured output, no search): turn that prose into ``[{i, target, source_refs[]}]``.
   ``source_refs`` are numbers into the list of pages of call 1, so the URL of a proposal always comes from the provider's
   metadata and never from words the model wrote (a model invents links). An answer with no page behind it proposes nothing.

A search tool and structured output do not mix on every model (FR-7.1), which is why there are two calls. The model is never
trusted: an answer that is not JSON, or an entry that is malformed, is dropped; a proposal needs a form and at least one real
page. Nothing here knows the database, the queue or HTTP (ADR-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import json_repair

from .providers.base import BadOutput, GroundingSource, LLMRequest, Provider
from .suggest import TermItem

__all__ = [
    "MAX_TERMS",
    "RESEARCH_PROMPT_VERSION",
    "ResearchDraft",
    "ResearchError",
    "ResearchOutcome",
    "research_terms",
]

# Bump when a prompt below changes: it is on the call record, so a change can be told apart.
RESEARCH_PROMPT_VERSION = "r1"
MAX_TERMS = 20  # terms one search call is asked about; the rest wait for the next run
MAX_TARGET = 80
MAX_NOTE = 80


class ResearchError(ValueError):
    """The provider cannot do the research, or its answer could not be used."""


@dataclass(frozen=True)
class ResearchDraft:
    """One term the web gave a form for: what the glossary would store, the cues that show the term, and the pages that
    back the form (from the provider's grounding metadata)."""

    payload: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    sources: tuple[GroundingSource, ...]


@dataclass(frozen=True)
class ResearchOutcome:
    drafts: tuple[ResearchDraft, ...]
    unanswered: tuple[TermItem, ...]  # asked about, nothing usable came back
    pages: int  # distinct pages the search used
    queries: tuple[str, ...]  # what the provider searched for
    calls: int  # model calls made


_SEARCH_SYSTEM = """\
You research how the proper names and invented terms of a story are written in subtitles and translations into the language
with code `{lang}`. Search the web. For each numbered item say the form the web uses for it in that language, one item per
line, and name the page that says so. Prefer the official release and fan wikis of the story. When the web gives a form
only for some items, say plainly that you found nothing for the others. Do not guess and do not invent a form."""

_READ_SYSTEM = """\
You turn a researched answer into data for a glossary of subtitles in the language with code `{lang}`.
ITEMS are numbered terms. ANSWER says how the web writes them; a mark such as [0, 2] after a passage lists the SOURCES that
back it. For each item the ANSWER really gives a form for, return `target` (that form, exactly as the ANSWER writes it, without
commentary or quotes), `source_refs` (the numbers of the SOURCES marked after the passages about this item) and `note` (a few
words on what it is). Leave out an item the ANSWER says nothing usable about. Never invent a form and never invent a source.
Output a single JSON object and nothing else.
Shape: {{"terms": [{{"i": <item number>, "target": "...", "source_refs": [<source number>], "note": "..."}}]}}"""

_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "target": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "integer"}},
                    "note": {"type": "string"},
                },
                "required": ["i", "target", "source_refs"],
            },
        }
    },
    "required": ["terms"],
}


def _fold(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _item_lines(items: Sequence[TermItem]) -> str:
    return "\n".join(f"{n}. {item.source}" + (f" ({item.kind})" if item.kind else "") for n, item in enumerate(items))


def _search(provider: Provider, items: Sequence[TermItem], *, lang: str, title: str):
    request = LLMRequest(
        system=_SEARCH_SYSTEM.format(lang=lang),
        user=f"STORY: {title or 'unknown'}\nITEMS:\n{_item_lines(items)}",
        task="research",
        web_search=True,
        meta={"kind": "research", "what": "search", "items": len(items)},
    )
    try:
        return provider.generate(request)
    except BadOutput as exc:  # an empty or refused answer is "nothing found", not a failure of the job
        raise ResearchError(str(exc) or "the search came back empty") from None


def _read(provider: Provider, items: Sequence[TermItem], sources: Sequence[GroundingSource], answer: str, *, lang: str) -> list[Any]:
    pages = "\n".join(f"[{n}] {s.domain or s.title or 'unknown site'}" for n, s in enumerate(sources))
    request = LLMRequest(
        system=_READ_SYSTEM.format(lang=lang),
        user=f"ITEMS:\n{_item_lines(items)}\n\nSOURCES:\n{pages}\n\nANSWER:\n{answer}",
        response_schema=_READ_SCHEMA if provider.caps.structured_output else None,
        task="research",
        json_output=True,
        meta={"kind": "research", "what": "read", "items": len(items)},
    )
    last = "no answer"
    for _ in range(2):
        try:
            response = provider.generate(request)
        except BadOutput as exc:
            last = str(exc) or "empty answer"
            continue
        try:
            data = json_repair.loads(response.text)
        except Exception:  # noqa: BLE001 - json_repair raises assorted errors on garbage
            data = None
        entries = data.get("terms") if isinstance(data, dict) else data if isinstance(data, list) else None
        if isinstance(entries, list):
            return entries
        last = "the answer is not the expected JSON"
    raise ResearchError(last)


def research_terms(
    search: Provider, read: Provider, items: Sequence[TermItem], *, lang: str, title: str = "",
) -> ResearchOutcome:
    """Look ``items`` up on the web (call 1, ``search``) and read what came back (call 2, ``read``); a term with a form and at
    least one page behind it is a draft. Provider errors propagate typed; an answer nothing can be taken from is a
    :class:`ResearchError`; a search that used no page ends after one call, with every item unanswered."""
    items = list(items)[:MAX_TERMS]
    if not items:
        return ResearchOutcome((), (), 0, (), 0)
    if not search.caps.web_search:
        raise ResearchError("this provider has no web search")
    found = _search(search, items, lang=lang, title=title)
    sources = tuple(found.grounding_sources)
    queries = tuple(found.search_queries)
    if not sources:  # prose with no page behind it is not research
        return ResearchOutcome((), tuple(items), 0, queries, 1)
    entries = _read(read, items, sources, found.cited_text or found.text, lang=lang)
    drafts: list[ResearchDraft] = []
    answered: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            n = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if not 0 <= n < len(items) or n in answered:
            continue
        item = items[n]
        target = _text(entry.get("target"), MAX_TARGET)
        refs = entry.get("source_refs")
        pages = []
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, int) and not isinstance(ref, bool) and 0 <= ref < len(sources) and sources[ref] not in pages:
                pages.append(sources[ref])
        if not target or not pages:
            continue
        answered.add(n)
        if _fold(target) == _fold(item.source):  # "keep it as it is" is no glossary entry worth a person's attention
            continue
        payload: dict[str, Any] = {"source": item.source, "target": target}
        note = _text(entry.get("note"), MAX_NOTE) or item.kind
        if note:
            payload["note"] = note
        drafts.append(ResearchDraft(payload, item.evidence, tuple(pages)))
    drafted = {_fold(d.payload["source"]) for d in drafts}
    unanswered = tuple(item for item in items if _fold(item.source) not in drafted)  # also the ones kept as they are
    return ResearchOutcome(tuple(drafts), unanswered, len(sources), queries, 2)
