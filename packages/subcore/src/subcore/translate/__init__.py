"""Translator: planner, prompt builder, validate + repair loop. Works from a bundle (ADR-16)."""

from .engine import (
    CallRecord,
    RenderedRequest,
    TranslateOptions,
    TranslationError,
    TranslationResult,
    ValidationIssue,
    make_bundle,
    options_of,
    render_requests,
    run,
    system_prompt,
    translate,
)
from .prompt import PROMPT_VERSION, PromptContext

__all__ = [
    "CallRecord",
    "PROMPT_VERSION",
    "PromptContext",
    "RenderedRequest",
    "TranslateOptions",
    "TranslationError",
    "TranslationResult",
    "ValidationIssue",
    "make_bundle",
    "options_of",
    "render_requests",
    "run",
    "system_prompt",
    "translate",
]
