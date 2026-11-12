"""Prompt builder (ARCHITECTURE §3.3 step 2).

System sections are ordered by priority, highest first:

1. core rules  2. target-language rules  3. glossary (authoritative)
4. extra context sections (story memory etc., added by later phases)  5. metadata

Core rules and the ``{i, text}`` structured-output contract are adapted from
gemini-srt-translator (MIT, Copyright (c) 2024 Matheus Castro); see NOTICE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..glossary import ActiveRule, Glossary

__all__ = ["PromptContext", "RESPONSE_SCHEMA", "build_system", "build_user", "format_targets", "language_name"]

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"}, "text": {"type": "string"}},
        "required": ["i", "text"],
    },
}

_LANGUAGE_NAMES = {
    "vi": "Vietnamese",
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "th": "Thai",
    "id": "Indonesian",
    "ar": "Arabic",
}


def language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code.lower().split("-")[0], code)


_CORE_RULES = """\
You are an expert subtitle translator. Translate the `text` of each requested item into {language}.

Input is a JSON list of items `{{"i": <int>, "text": <string>}}`. Output is a JSON list of
`{{"i": <int>, "text": <string>}}` objects, one for every requested `i`, in the same order.

Rules:
- Translate the text only. Never change, add, drop, merge, split or reorder items; never invent an `i`.
- Keep formatting exactly: HTML tags (<i>, <b>, <u>, <font ...>) stay in the same number and nesting,
  `{{\\an8}}`-style overrides and line breaks (\\n) stay where they are logically.
- Items outside the requested list are context only. Use them to resolve who speaks, gender, and tone,
  but do not output them.
- Subtitles are read quickly: keep lines short and natural, at most two lines per item.
- Do not add notes, explanations or translator comments."""

_VI_RULES = """\
Vietnamese rules:
- Write natural spoken Vietnamese, not a word-for-word rendering.
- Pick pronouns (tôi, anh, chị, em, cô, chú, bác, ông, bà, cậu, mình, ...) from the speakers' relationship,
  relative age, gender, hierarchy and closeness, not by translating "I/you" mechanically. Once a pair of
  speakers has a form of address, keep it for the whole episode unless the context or an address rule
  below changes it.
- When the relationship is unknown, stay neutral and polite (e.g. tôi–anh/chị for adult strangers) and
  switch only on clear evidence in the dialogue.
- Keep proper names in their original Latin spelling unless the glossary gives a translation."""

_TARGET_RULES = {"vi": _VI_RULES}


@dataclass
class PromptContext:
    target_lang: str = "vi"
    # Display name sent to the model when the code alone is ambiguous (e.g. "Simplified Chinese").
    target_name: str | None = None
    source_lang: str | None = None
    glossary: Glossary | None = None
    season: int | None = None
    episode: int | None = None
    title: str = ""
    overview: str = ""
    # Extra (title, body) sections placed after the glossary and before metadata.
    extra_sections: list[tuple[str, str]] = field(default_factory=list)
    # Address rules already resolved for this episode (None = compute from the glossary).
    active_rules: list[ActiveRule] | None = None


def _glossary_section(glossary: Glossary, active: list[ActiveRule]) -> str:
    lines = ["Authoritative: these rules override anything inferred from context."]
    if glossary.style:
        lines.append(f"Style: {glossary.style}")
    if glossary.terms:
        lines.append("Terms (a LOCKED term's translation must appear verbatim whenever the source uses the term):")
        for t in glossary.terms:
            extra = f" (also written: {', '.join(t.aliases)})" if t.aliases else ""
            tag = " [LOCKED]" if t.locked else ""
            note = f" — {t.note}" if t.note else ""
            lines.append(f'- "{t.source}"{extra} → "{t.target}"{tag}{note}')
    if glossary.characters:
        lines.append("Characters:")
        for c in glossary.characters:
            bits = [c.name]
            if c.target and c.target != c.name:
                bits.append(f'→ "{c.target}"')
            if c.gender:
                bits.append(f"[{c.gender}]")
            if c.description:
                bits.append(f"— {c.description}")
            lines.append("- " + " ".join(bits))
    if active:
        lines.append('Forms of address, written "self–other" from the speaker\'s point of view:')
        for ar in active:
            r = ar.rule
            scope = []
            if ar.from_cue is not None:
                scope.append(f"from item {ar.from_cue} on")
            if ar.until_cue is not None:
                scope.append(f"before item {ar.until_cue}")
            where = f" ({', '.join(scope)})" if scope else ""
            note = f" — {r.note}" if r.note else ""
            if r.a_to_b:
                lines.append(f'- {r.a} speaking to {r.b}: "{r.a_to_b}"{where}{note}')
            if r.b_to_a:
                lines.append(f'- {r.b} speaking to {r.a}: "{r.b_to_a}"{where}{note}')
    return "\n".join(lines)


def build_system(ctx: PromptContext, cues: list[Any] | None = None) -> str:
    """Assemble the system prompt. ``cues`` is only used to place timed address rules."""
    sections: list[tuple[str, str]] = [
        ("Core rules", _CORE_RULES.format(language=ctx.target_name or language_name(ctx.target_lang))),
    ]
    lang_rules = _TARGET_RULES.get(ctx.target_lang.lower().split("-")[0])
    if lang_rules:
        sections.append(("Target-language rules", lang_rules))
    if ctx.glossary:
        # Terms carry a story-order window (FR-11.2.3): only those in effect for this episode.
        glossary = ctx.glossary.for_episode(ctx.season, ctx.episode)
        if not glossary.is_empty():
            active = ctx.active_rules
            if active is None:
                active = glossary.active_address_rules(ctx.season, ctx.episode, cues)
            sections.append(("Glossary", _glossary_section(glossary, active)))
    sections.extend(ctx.extra_sections)

    meta = []
    if ctx.title:
        meta.append(f"Title: {ctx.title}")
    if ctx.season is not None and ctx.episode is not None:
        meta.append(f"Episode: season {ctx.season}, episode {ctx.episode}")
    if ctx.source_lang:
        meta.append(f"Source language: {language_name(ctx.source_lang)}")
    if ctx.overview:
        meta.append(f"Overview: {ctx.overview}")
    if meta:
        sections.append(("Metadata", "\n".join(meta)))

    return "\n\n".join(f"## {n}. {title}\n{body}" for n, (title, body) in enumerate(sections, start=1))


def format_targets(ids: list[int]) -> str:
    """``[1,2,3,7,9,10]`` → ``"1-3, 7, 9-10"``."""
    out: list[str] = []
    start = prev = None
    for i in sorted(ids):
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = i
    if start is not None:
        out.append(f"{start}-{prev}" if prev > start else str(start))
    return ", ".join(out)


def build_user(
    source: dict[int, str],
    targets: list[int],
    notes: dict[int, str] | None = None,
    reference: dict[int, str] | None = None,
) -> str:
    """User message: the source items (context) plus the exact ids to translate.

    ``reference`` holds finished translations of items that are *not* requested (locked edits,
    cues outside a retranslated range): shown so names, tone and forms of address stay consistent.
    Timecodes and SRT indexes are never included (FR-3.1).
    """
    items = "\n".join(json.dumps({"i": i, "text": text}, ensure_ascii=False) for i, text in source.items())
    parts = [f"SOURCE ITEMS (one JSON object per line):\n{items}"]
    if reference:
        done = "\n".join(
            json.dumps({"i": i, "text": text}, ensure_ascii=False) for i, text in sorted(reference.items())
        )
        parts.append(
            "ALREADY TRANSLATED (final; do not output these items; keep names, tone and forms of address "
            f"consistent with them):\n{done}"
        )
    parts.append(
        "TASK: translate ONLY these items and return one {\"i\", \"text\"} object for each, in order: "
        + format_targets(targets)
        + ("" if set(targets) != set(source) else "  (that is every item above)")
    )
    if notes:
        listed = "\n".join(f"- i={i}: {msg}" for i, msg in sorted(notes.items()))
        parts.append(f"FIX THESE (your previous answer for them was rejected):\n{listed}")
    return "\n\n".join(parts)
