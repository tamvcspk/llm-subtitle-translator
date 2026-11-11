"""Translate a cue list: plan → call → validate → repair → split (FR-2, FR-3).

Output cues are built from the *source* cues with only ``text`` replaced, so
timecodes, indexes and cue count are preserved by construction (FR-3.1); the
LLM only ever sees ``{i, text}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .. import srt
from ..providers.base import BadOutput, LLMRequest, Provider
from . import alignment, planner
from .prompt import RESPONSE_SCHEMA, PromptContext, build_system, build_user
from .validate import check_cue, parse_reply

__all__ = ["TranslateOptions", "CallRecord", "TranslationResult", "TranslationError", "translate"]


@dataclass
class TranslateOptions:
    output_factor: float = planner.DEFAULT_OUTPUT_FACTOR
    min_part_cues: int = planner.MIN_PART_CUES
    repair_rounds: int = 2  # FR-3.3
    context_window: int = 5  # neighbours shown around each cue being repaired
    max_split_depth: int = 1  # splitting after failed repairs: one level
    temperature: float | None = None


@dataclass
class CallRecord:
    kind: str  # translate | repair | split
    targets: int
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    outcome: str = "ok"  # ok | bad_output


@dataclass
class TranslationResult:
    cues: list[srt.Cue]
    calls: list[CallRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def tokens_in(self) -> int:
        return sum(c.tokens_in for c in self.calls)

    @property
    def tokens_out(self) -> int:
        return sum(c.tokens_out for c in self.calls)


class TranslationError(Exception):
    """Some cues could not be translated validly even after repair and splitting."""

    def __init__(self, failed: dict[int, str]):
        self.failed = failed
        shown = "; ".join(f"cue {i}: {why}" for i, why in sorted(failed.items())[:5])
        more = f" (+{len(failed) - 5} more)" if len(failed) > 5 else ""
        super().__init__(f"{len(failed)} cue(s) failed validation after repair: {shown}{more}")


def _reason(verdict_errors: list[str], terms: list) -> str:
    bits = list(verdict_errors)
    for t in terms:
        bits.append(f'locked term "{t.source}" must be translated as "{t.target}"')
    return "; ".join(bits)


class _Run:
    def __init__(
        self,
        provider: Provider,
        cues: list[srt.Cue],
        ctx: PromptContext,
        options: TranslateOptions,
        on_progress: Callable[[int, int], None] | None,
    ) -> None:
        self.provider = provider
        self.cues = cues
        self.ctx = ctx
        self.options = options
        self.on_progress = on_progress
        self.system = build_system(ctx, cues)
        self.glossary = ctx.glossary
        self.source = {n: c.text for n, c in enumerate(cues, start=1) if c.text.strip()}
        self.out: dict[int, str] = {}
        self.calls: list[CallRecord] = []
        self.warnings: list[str] = []
        self.total = len(self.source)

    # -- one model call ---------------------------------------------------
    def _call(self, kind: str, targets: list[int], context: dict[int, str], notes: dict[int, str]):
        req = LLMRequest(
            system=self.system,
            user=build_user(context, targets, notes),
            response_schema=RESPONSE_SCHEMA if self.provider.caps.structured_output else None,
            temperature=self.options.temperature,
            task="translate",
            meta={"kind": kind, "targets": list(targets), "source": dict(context), "notes": dict(notes)},
        )
        try:
            response = self.provider.generate(req)
        except BadOutput:
            self.calls.append(CallRecord(kind, len(targets), outcome="bad_output"))
            return parse_reply("", set(targets))
        self.calls.append(
            CallRecord(kind, len(targets), response.tokens_in, response.tokens_out, response.model)
        )
        return parse_reply(response.text, set(targets))

    def _window(self, failing: list[int]) -> dict[int, str]:
        w = self.options.context_window
        keep: set[int] = set()
        ordered = sorted(self.source)
        position = {n: k for k, n in enumerate(ordered)}
        for n in failing:
            k = position[n]
            keep.update(ordered[max(0, k - w) : k + w + 1])
        return {n: self.source[n] for n in ordered if n in keep}

    # -- validate a reply -------------------------------------------------
    def _apply(self, targets: list[int], reply, candidates: dict[int, str]) -> dict[int, str]:
        """Accept valid cues into ``self.out``; return ``{id: reason}`` for the rest."""
        failing: dict[int, str] = {}
        for n in targets:
            verdict = check_cue(self.source[n], reply.items.get(n), self.glossary)
            if verdict.ok:
                self.out[n] = reply.items[n]
                candidates.pop(n, None)
                for w in verdict.warnings:
                    self.warnings.append(f"cue {n}: {w}")
            else:
                failing[n] = _reason(verdict.errors, verdict.terms)
                if not verdict.errors and n in reply.items:
                    candidates[n] = reply.items[n]  # only glossary issues: usable as a last resort
                else:
                    candidates.pop(n, None)
        if self.on_progress:
            self.on_progress(len(self.out), self.total)
        return failing

    # -- one part, with repairs and optional split ------------------------
    def run_part(self, ids: list[int], *, kind: str, parts_used: int, depth: int) -> None:
        candidates: dict[int, str] = {}
        reply = self._call(kind, ids, self.source, {})
        failing = self._apply(ids, reply, candidates)

        for _ in range(self.options.repair_rounds):
            for n, why in alignment.find_misaligned(ids, self.source, self.out).items():
                failing.setdefault(n, why)
            if not failing:
                return
            reply = self._call("repair", sorted(failing), self._window(sorted(failing)), failing)
            failing = self._apply(sorted(failing), reply, candidates)

        # Drift is a heuristic, never a failure: cues that already hold a (possibly shifted)
        # translation stay, with a warning for the reviewer.
        for n in [n for n in failing if n in self.out and n not in candidates]:
            del failing[n]
        drifting = sorted(alignment.find_misaligned(ids, self.source, self.out))
        if drifting:
            self.warnings.append(
                f"{len(drifting)} cue(s) (cue {drifting[0]} to {drifting[-1]}) may carry the "
                "translation of a neighbouring cue; review them"
            )

        if not failing:
            return
        # Glossary-only problems never fail a job: keep the best candidate and warn.
        for n in sorted(failing):
            if n in candidates:
                self.out[n] = candidates[n]
                self.warnings.append(f"cue {n}: kept despite glossary issue ({failing[n]})")
                del failing[n]
        if not failing:
            return
        hard = sorted(failing)
        if depth < self.options.max_split_depth and len(hard) > 1:
            force = min(len(hard), max(2, parts_used * 2))
            subparts = planner.plan_parts(
                self.cues, hard, self.provider.caps.max_output_tokens, force_parts=force
            )
            for sub in subparts:
                self.run_part(sub, kind="split", parts_used=len(subparts), depth=depth + 1)
            return
        raise TranslationError({n: failing[n] for n in hard})


def translate(
    provider: Provider,
    cues: list[srt.Cue],
    ctx: PromptContext | None = None,
    options: TranslateOptions | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> TranslationResult:
    """Translate ``cues`` and return new cues with identical index/timing.

    Raises :class:`TranslationError` if cues stay invalid after two repair rounds and
    one split; provider errors (``RateLimited`` etc.) propagate to the caller.
    """
    ctx = ctx or PromptContext()
    options = options or TranslateOptions()
    run = _Run(provider, cues, ctx, options, on_progress)
    targets = sorted(run.source)
    parts = planner.plan_parts(
        cues,
        targets,
        provider.caps.max_output_tokens,
        output_factor=options.output_factor,
        min_part_cues=options.min_part_cues,
    )
    for part in parts:
        run.run_part(part, kind="translate", parts_used=len(parts), depth=0)

    translated = [cue.with_text(run.out.get(n, cue.text)) for n, cue in enumerate(cues, start=1)]
    return TranslationResult(cues=translated, calls=run.calls, warnings=run.warnings)
