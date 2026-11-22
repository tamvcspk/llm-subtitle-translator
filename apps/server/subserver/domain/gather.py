"""Gather a translation bundle from what the worker knows about one episode (FR-21.2, ADR-16).

This is the worker's half of the bundle contract: it reads the episode's rules (glossary file plus corrections),
its story memory and its metadata, and hands the engine one :class:`~subcore.bundle.TranslationBundle`. The engine
never sees the database; the CLI never sees this module. Pure: nothing here reads or writes, so the same inputs
give the same bundle, and its hash, every time.
"""

from __future__ import annotations

from typing import Any, Iterable

from subcore import srt
from subcore.bundle import TranslationBundle
from subcore.translate import PromptContext, TranslateOptions
from subcore.translate.engine import make_bundle

from .rules import UnitRules


def gather(
    *,
    cues: list[srt.Cue],
    rules: UnitRules,
    unit: Any,
    title: str,
    overview: str,
    target_code: str,
    target_name: str,
    source_code: str,
    extra_sections: Iterable[tuple[str, str]] = (),
    model: str,
    max_output_tokens: int,
    temperature: float | None,
    only: Iterable[int] | None,
    keep: dict[int, str] | None,
    few_shot: Iterable[tuple[str, str]] = (),
    glossary_filter_threshold: int = 50,
) -> TranslationBundle:
    """The bundle of one attempt. ``extra_sections`` are the story memory's sections (corrections come from ``rules``,
    and so do the effective style and the voices of the characters the dialogue names)."""
    context = PromptContext(
        target_lang=target_code,
        target_name=target_name or None,
        source_lang=source_code or None,
        overview=overview,
        glossary=rules.glossary,
        active_rules=rules.active_rules,
        # Corrections come before the machine's story so they win; both follow the glossary.
        extra_sections=[*rules.extra_sections, *extra_sections],
        season=unit.season if unit else None,
        episode=unit.episode if unit else None,
        title=title,
        few_shot=list(few_shot),
        style=rules.style,
        voices=list(rules.voices),
    )
    return make_bundle(
        cues,
        context,
        TranslateOptions(temperature=temperature, glossary_filter_threshold=glossary_filter_threshold),
        only=only,
        keep=keep,
        model=model,
        max_output_tokens=max_output_tokens,
    )
