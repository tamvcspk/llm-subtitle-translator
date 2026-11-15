"""Translate a bundle: plan → call → validate → repair → split (FR-2, FR-3, ADR-16).

The engine works only from a :class:`~subcore.bundle.TranslationBundle`. ``make_bundle`` resolves a caller's
context into one, ``run`` translates it, ``render_requests`` shows the first request of every part without
calling a provider (``subcore render-prompt``). ``translate`` is the compatibility entry point: it makes the
bundle and runs it, so every path shares one prompt builder.

Output cues are built from the *source* cues with only ``text`` replaced, so timecodes, indexes and cue count
are preserved by construction (FR-3.1); the LLM only ever sees ``{i, text}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .. import srt
from ..bundle import Params, TranslationBundle
from ..glossary import Glossary
from ..providers.base import BadOutput, Blocked, LLMRequest, Provider
from . import alignment, planner
from .prompt import PROMPT_VERSION, RESPONSE_SCHEMA, PromptContext, build_user, render_system
from .validate import check_cue, parse_reply

__all__ = [
    "TranslateOptions",
    "CallRecord",
    "TranslationResult",
    "TranslationError",
    "ValidationIssue",
    "RenderedRequest",
    "make_bundle",
    "options_of",
    "render_requests",
    "run",
    "system_prompt",
    "translate",
]


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
    outcome: str = "ok"  # ok | bad_output | blocked


@dataclass(frozen=True)
class ValidationIssue:
    """One problem the validator found in a model reply, whether or not a repair later fixed it (FR-19.4).

    ``kind`` is missing | empty | tags | lines | glossary; ``call`` is the kind of call that produced the reply
    (translate | repair | split); ``error`` is False for a warning that never forces a repair.
    """

    cue: int
    kind: str
    call: str
    error: bool
    source: str = ""
    candidate: str = ""
    detail: str = ""


@dataclass
class TranslationResult:
    cues: list[srt.Cue]
    calls: list[CallRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def tokens_in(self) -> int:
        return sum(c.tokens_in for c in self.calls)

    @property
    def tokens_out(self) -> int:
        return sum(c.tokens_out for c in self.calls)


@dataclass(frozen=True)
class RenderedRequest:
    """The first request of one part, as it would be sent (no repairs, no provider)."""

    part: int
    targets: tuple[int, ...]
    system: str
    user: str


class TranslationError(Exception):
    """Some cues could not be translated validly even after repair and splitting."""

    def __init__(self, failed: dict[int, str]):
        self.failed = failed
        self.issues: list[ValidationIssue] = []  # what the validator found on the way; filled in by ``run``
        self.blocked = 0  # how many calls the provider refused for their content; filled in by ``run``
        shown = "; ".join(f"cue {i}: {why}" for i, why in sorted(failed.items())[:5])
        more = f" (+{len(failed) - 5} more)" if len(failed) > 5 else ""
        super().__init__(f"{len(failed)} cue(s) failed validation after repair: {shown}{more}")


def _reason(verdict_errors: list[str], terms: list) -> str:
    bits = list(verdict_errors)
    for t in terms:
        bits.append(f'locked term "{t.source}" must be translated as "{t.target}"')
    return "; ".join(bits)


# ---------------------------------------------------------------- bundles


def options_of(bundle: TranslationBundle) -> TranslateOptions:
    """The planning and repair settings a bundle carries."""
    p = bundle.params
    return TranslateOptions(
        output_factor=p.output_factor, min_part_cues=p.min_part_cues, repair_rounds=p.repair_rounds,
        context_window=p.context_window, max_split_depth=p.max_split_depth, temperature=p.temperature,
    )


def make_bundle(
    cues: Sequence[srt.Cue],
    ctx: PromptContext | None = None,
    options: TranslateOptions | None = None,
    *,
    only: Iterable[int] | None = None,
    keep: dict[int, str] | None = None,
    model: str = "",
    max_output_tokens: int | None = None,
) -> TranslationBundle:
    """Resolve a caller's context into a bundle. Pure: nothing is read or called here.

    The glossary is narrowed to the episode and the episode's address rules are placed on cues; the result is what
    the prompt and the planner will see, and nothing else.
    """
    ctx = ctx or PromptContext()
    options = options or TranslateOptions()
    glossary = ctx.glossary.for_episode(ctx.season, ctx.episode) if ctx.glossary else Glossary()
    if glossary.is_empty():
        glossary, active = Glossary(), ()
    elif ctx.active_rules is not None:
        active = tuple(ctx.active_rules)
    else:
        active = tuple(glossary.active_address_rules(ctx.season, ctx.episode, list(cues)))
    if max_output_tokens is None:
        from ..providers.base import Capabilities

        max_output_tokens = Capabilities().max_output_tokens
    total = len(cues)
    return TranslationBundle(
        cues=tuple(cues),
        target_lang=ctx.target_lang,
        target_name=ctx.target_name or "",
        source_lang=ctx.source_lang or "",
        title=ctx.title or "",
        overview=ctx.overview or "",
        season=ctx.season,
        episode=ctx.episode,
        glossary=glossary,
        active_rules=active,
        sections=tuple((str(t), str(b)) for t, b in ctx.extra_sections),
        few_shot=tuple((str(s), str(t)) for s, t in ctx.few_shot),
        requested=None if only is None else tuple(sorted({int(n) for n in only})),
        keep={n: text for n, text in (keep or {}).items() if 1 <= n <= total},
        params=Params(
            model=model,
            prompt_version=PROMPT_VERSION,
            temperature=options.temperature,
            max_output_tokens=int(max_output_tokens),
            output_factor=options.output_factor,
            min_part_cues=options.min_part_cues,
            repair_rounds=options.repair_rounds,
            context_window=options.context_window,
            max_split_depth=options.max_split_depth,
        ),
    )


def system_prompt(ctx: PromptContext | None = None, cues: Sequence[srt.Cue] | None = None) -> str:
    """The system prompt a context would get (for callers that only need the text)."""
    return render_system(make_bundle(list(cues or ()), ctx))


class _Run:
    def __init__(
        self,
        provider: Provider,
        bundle: TranslationBundle,
        on_progress: Callable[[int, int], None] | None,
    ) -> None:
        self.provider = provider
        self.bundle = bundle
        self.cues = list(bundle.cues)
        self.options = options_of(bundle)
        self.on_progress = on_progress
        self.system = render_system(bundle)
        self.glossary = bundle.glossary if not bundle.glossary.is_empty() else None
        self.source = {n: c.text for n, c in enumerate(self.cues, start=1) if c.text.strip()}
        self.keep = dict(bundle.keep)
        wanted = None if bundle.requested is None else set(bundle.requested)
        self.targets = [
            n for n in sorted(self.source) if n not in self.keep and (wanted is None or n in wanted)
        ]
        self.out: dict[int, str] = {}
        self.calls: list[CallRecord] = []
        self.warnings: list[str] = []
        self.issues: list[ValidationIssue] = []
        self.part_no = 0  # which part of the episode is being worked on (0-based), for the call record
        self.blocked = 0  # calls the provider refused for their content (FR-17.2)
        self.total = len(self.targets)

    # -- one model call ---------------------------------------------------
    def _call(self, kind: str, targets: list[int], context: dict[int, str], notes: dict[int, str]):
        reference = {n: text for n, text in self.keep.items() if n in context}
        req = LLMRequest(
            system=self.system,
            user=build_user(context, targets, notes, reference),
            response_schema=RESPONSE_SCHEMA if self.provider.caps.structured_output else None,
            temperature=self.bundle.params.temperature,
            task="translate",
            json_output=True,
            meta={
                "kind": kind, "targets": list(targets), "source": dict(context), "notes": dict(notes),
                "batch": self.part_no,
            },
        )
        try:
            response = self.provider.generate(req)
        except BadOutput as exc:
            blocked = isinstance(exc, Blocked)
            self.blocked += blocked
            self.calls.append(CallRecord(kind, len(targets), outcome="blocked" if blocked else "bad_output"))
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
    def _apply(self, targets: list[int], reply, candidates: dict[int, str], kind: str = "translate") -> dict[int, str]:
        """Accept valid cues into ``self.out``; return ``{id: reason}`` for the rest."""
        failing: dict[int, str] = {}
        for n in targets:
            verdict = check_cue(self.source[n], reply.items.get(n), self.glossary)
            for found in verdict.kinds:
                self.issues.append(ValidationIssue(
                    cue=n, kind=found, call=kind, error=found != "lines", source=self.source[n],
                    candidate=reply.items.get(n, ""), detail=_reason(verdict.errors, verdict.terms) or "; ".join(verdict.warnings),
                ))
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
        failing = self._apply(ids, reply, candidates, kind)

        for _ in range(self.options.repair_rounds):
            for n, why in alignment.find_misaligned(ids, self.source, self.out).items():
                failing.setdefault(n, why)
            if not failing:
                return
            reply = self._call("repair", sorted(failing), self._window(sorted(failing)), failing)
            failing = self._apply(sorted(failing), reply, candidates, "repair")

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
                self.cues, hard, self.bundle.params.max_output_tokens,
                output_factor=self.options.output_factor, min_part_cues=self.options.min_part_cues,
                force_parts=force,
            )
            for sub in subparts:
                self.run_part(sub, kind="split", parts_used=len(subparts), depth=depth + 1)
            return
        raise TranslationError({n: failing[n] for n in hard})


def _parts(bundle: TranslationBundle, targets: list[int], options: TranslateOptions) -> list[list[int]]:
    if not targets:
        return []
    return planner.plan_parts(
        list(bundle.cues),
        targets,
        bundle.params.max_output_tokens,
        output_factor=options.output_factor,
        min_part_cues=options.min_part_cues,
    )


def render_requests(bundle: TranslationBundle) -> list[RenderedRequest]:
    """The first request of each part, rendered exactly as ``run`` would send it. Calls no provider (FR-21.3)."""
    options = options_of(bundle)
    source = {n: c.text for n, c in enumerate(bundle.cues, start=1) if c.text.strip()}
    keep = dict(bundle.keep)
    wanted = None if bundle.requested is None else set(bundle.requested)
    targets = [n for n in sorted(source) if n not in keep and (wanted is None or n in wanted)]
    system = render_system(bundle)
    out = []
    for number, part in enumerate(_parts(bundle, targets, options)):
        reference = {n: text for n, text in keep.items() if n in source}
        out.append(RenderedRequest(number, tuple(part), system, build_user(source, part, {}, reference)))
    return out


def run(
    bundle: TranslationBundle,
    provider: Provider,
    on_progress: Callable[[int, int], None] | None = None,
) -> TranslationResult:
    """Translate a bundle. Returns the bundle's cues with new text (FR-3.1).

    Raises :class:`TranslationError` if cues stay invalid after two repair rounds and one split; provider errors
    (``RateLimited`` etc.) propagate to the caller.
    """
    engine = _Run(provider, bundle, on_progress)
    if engine.targets:
        parts = _parts(bundle, engine.targets, engine.options)
        try:
            for number, part in enumerate(parts):
                engine.part_no = number
                engine.run_part(part, kind="translate", parts_used=len(parts), depth=0)
        except TranslationError as exc:
            exc.issues = engine.issues
            exc.blocked = engine.blocked
            raise

    translated = [
        cue.with_text(engine.out.get(n, engine.keep.get(n, cue.text)))
        for n, cue in enumerate(engine.cues, start=1)
    ]
    return TranslationResult(cues=translated, calls=engine.calls, warnings=engine.warnings, issues=engine.issues)


def translate(
    provider: Provider,
    cues: list[srt.Cue],
    ctx: PromptContext | None = None,
    options: TranslateOptions | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    only: Iterable[int] | None = None,
    keep: dict[int, str] | None = None,
) -> TranslationResult:
    """Translate ``cues`` and return new cues with identical index/timing.

    ``only`` restricts the request to those cue ordinals (1-based); the whole source is still
    sent as context (FR-11.3.2). ``keep`` maps ordinals to final texts that are copied to the
    output verbatim, never requested, and shown to the model as reference translations (locked
    cue edits, cues outside a retranslated range). Any other cue keeps its source text.

    A thin wrapper: it makes the bundle and runs it (:func:`make_bundle`, :func:`run`).
    """
    bundle = make_bundle(
        cues, ctx, options, only=only, keep=keep,
        model=str(getattr(provider, "model", "") or ""),
        max_output_tokens=provider.caps.max_output_tokens,
    )
    return run(bundle, provider, on_progress)
