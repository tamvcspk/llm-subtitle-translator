"""Declarative subcommands for manual use and quality checks (FR-21.3). Knows nothing of the web, jobs or the database.

Every translating command works from a bundle: ``--bundle FILE`` loads one, ``subcore bundle`` writes one, and
flags given together with a bundle override it (a flag wins over the bundle, FR-21.3).

    subcore translate Movie.en.srt --glossary g.yaml --provider fake      # → Movie.vi.srt + Movie.vi.report.json
    subcore bundle    Movie.en.srt --glossary g.yaml --title Show -o b.json
    subcore translate Movie.en.srt --bundle b.json --provider fake
    subcore render-prompt --bundle b.json                                 # prints the requests, calls nothing
    subcore validate  Movie.en.srt Movie.vi.srt --glossary g.yaml         # exit 1 on review errors
    subcore compare   Movie.en.srt --bundle a.json --bundle b.json --provider fake
    subcore compare   Movie.en.srt --bundle a.json --golden golden.vi.srt

``subcore memory ep1.srt ep2.srt ...`` prints the story facts the memory would extract (P4).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import compare as compare_mod
from . import glossary as glossary_mod
from . import srt
from .bundle import BundleError, TranslationBundle
from .memory import FactsError
from .providers import FakeProvider
from .providers.base import Provider, ProviderError
from .translate import PromptContext, TranslateOptions, TranslationError, TranslationResult
from .translate.engine import make_bundle, options_of, render_requests, run
from .translate.review import review

_LANG_SUFFIX_RE = re.compile(r"\.[A-Za-z]{2,3}$")
_DEFAULT_TARGET = "vi"
# The variable each provider reads its key from unless ``--api-key-env`` names another. A key of one provider is never
# offered to another provider's server (R11-1): openai_compat does not fall back to the Gemini variable.
_DEFAULT_KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai_compat": "OPENAI_COMPAT_API_KEY"}


def default_output_path(src: Path, target: str, source_lang: str | None) -> Path:
    """``Movie.en.srt`` → ``Movie.vi.srt`` (appends the language if there is none to replace)."""
    stem = src.stem
    if source_lang and stem.lower().endswith(f".{source_lang.lower()}"):
        stem = stem[: -(len(source_lang) + 1)]
    elif not source_lang and _LANG_SUFFIX_RE.search(stem):
        stem = _LANG_SUFFIX_RE.sub("", stem)
    return src.with_name(f"{stem}.{target}.srt")


def resolve_api_key(flag: str | None, env_name: str, key_file: str | None, environ=None, also=()) -> str:
    """FR-21.4: the key comes from the flag, then the environment, then the file. A flag leaks through shell history
    and ``ps``, so the docs point people at the other two. ``also`` names more variables of the same provider that
    may hold the key; a key of one provider is never offered to another."""
    environ = os.environ if environ is None else environ
    if flag:
        return flag.strip()
    for name in (env_name, *also):
        if environ.get(name, "").strip():
            return environ[name].strip()
    if key_file:
        for line in Path(key_file).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                return line.strip()
    return ""


class _Fail(Exception):
    """A user-facing failure: printed, exit code 2."""


def _key_env(args: argparse.Namespace) -> str:
    return getattr(args, "api_key_env", None) or _DEFAULT_KEY_ENV.get(getattr(args, "provider", ""), "GEMINI_API_KEY")


def _api_key(args: argparse.Namespace, also=()) -> str:
    try:
        return resolve_api_key(args.api_key, _key_env(args), args.api_key_file, also=also)
    except (OSError, ValueError) as exc:  # a missing or unreadable key file: say so, without a traceback
        raise _Fail(f"--api-key-file: the key file cannot be read ({_describe(exc)})") from None


def _describe(exc: Exception) -> str:
    """One line for an input error. An OS error says what went wrong, not the whole traceback."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    if isinstance(exc, UnicodeDecodeError):
        return "it is not UTF-8 text"
    return str(exc)


# ---------------------------------------------------------------- inputs


def _load_bundle(path: str | None) -> TranslationBundle | None:
    if not path:
        return None
    try:
        return TranslationBundle.from_json(Path(path).read_text(encoding="utf-8-sig"))
    except BundleError as exc:
        raise _Fail(f"bundle {path}: {exc}") from None
    except (OSError, ValueError) as exc:  # unreadable, or not UTF-8
        raise _Fail(f"bundle {path}: {_describe(exc)}") from None


def _load_glossary(path: str) -> glossary_mod.Glossary:
    try:
        return glossary_mod.load(path)
    except glossary_mod.GlossaryError as exc:
        raise _Fail(str(exc)) from None
    except (OSError, ValueError) as exc:
        raise _Fail(f"glossary {path}: {_describe(exc)}") from None


def _inputs(args: argparse.Namespace, cues: list[srt.Cue] | None):
    """Turn the bundle, the flags and the cues into (cues, context, options, only, keep, model, max_output)."""
    base = _load_bundle(getattr(args, "bundle", None))
    if base is not None:
        ctx = PromptContext(
            target_lang=base.target_lang,
            target_name=base.target_name or None,
            source_lang=base.source_lang or None,
            glossary=base.glossary,
            season=base.season,
            episode=base.episode,
            title=base.title,
            overview=base.overview,
            extra_sections=list(base.sections),
            active_rules=list(base.active_rules),
            few_shot=list(base.few_shot),
        )
        options = options_of(base)
        if getattr(args, "season", None) is not None or getattr(args, "episode", None) is not None:
            raise _Fail(
                "the bundle's glossary is already narrowed to its own episode; "
                "make a new bundle with `subcore bundle` for another one"
            )
        only, keep = base.requested, dict(base.keep)
        model, max_output = base.params.model, base.params.max_output_tokens
        if cues is None:
            cues = list(base.cues)
        elif len(cues) != len(base.cues):
            raise _Fail(f"the bundle was made for {len(base.cues)} cues, the file has {len(cues)}")
    else:
        ctx = PromptContext(target_lang=_DEFAULT_TARGET)
        options = TranslateOptions()
        only, keep, model, max_output = None, {}, "", None
        if cues is None:
            raise _Fail("give a subtitle file or --bundle")

    # Flags that were given win over the bundle (FR-21.3).
    if args.target is not None:
        if args.target.lower() != ctx.target_lang.lower():
            ctx.target_name = None  # the bundle's display name described the old language (R11-2)
        ctx.target_lang = args.target
    if args.source is not None:
        ctx.source_lang = args.source
    if args.title is not None:
        ctx.title = args.title
    if args.overview is not None:
        ctx.overview = args.overview
    if args.season is not None or args.episode is not None:
        ctx.season = args.season if args.season is not None else ctx.season
        ctx.episode = args.episode if args.episode is not None else ctx.episode
    if args.glossary:
        ctx.glossary = _load_glossary(args.glossary)
    if args.glossary or args.season is not None or args.episode is not None:
        ctx.active_rules = None  # recomputed for the new glossary and episode
    if args.temperature is not None:
        options.temperature = args.temperature
    if args.max_output_tokens is not None:
        max_output = args.max_output_tokens
    if getattr(args, "model", None):
        model = args.model
    return cues if cues is not None else list(base.cues), ctx, options, only, keep, model, max_output


def _bundle_for(args: argparse.Namespace, cues: list[srt.Cue] | None) -> TranslationBundle:
    """The bundle the flags and ``--bundle`` describe. The model and the output limit are what the user or the bundle
    set, and empty otherwise: the provider that runs it supplies them, so the bundle keeps its hash (R11-3, FR-21.3)."""
    cues, ctx, options, only, keep, model, max_output = _inputs(args, cues)
    return make_bundle(cues, ctx, options, only=only, keep=keep, model=model, max_output_tokens=max_output)


def _build_provider(args: argparse.Namespace, bundle_model: str | None = None, target: str | None = None) -> Provider:
    """The provider for a run. ``bundle_model`` is the model the bundle carries (``--model`` wins over it); ``target``
    is the bundle's language, which the ``fake`` provider writes in front of every line."""
    model = getattr(args, "model", None) or bundle_model
    if args.provider == "fake":
        return FakeProvider(prefix=f"[{target or args.target or _DEFAULT_TARGET}] ")
    if args.provider == "openai_compat":
        from .providers.openai_compat import OpenAICompatProvider

        if not args.base_url or not model:
            raise SystemExit("error: --provider openai_compat needs --base-url and --model")
        # A local server (Ollama, LM Studio) needs no key: the variable may be unset.
        key = _api_key(args)
        try:
            return OpenAICompatProvider(args.base_url, key, model)
        except ValueError as exc:
            raise SystemExit(f"error: {exc}") from None
    from .providers.gemini import DEFAULT_MODEL, GeminiProvider

    key = _api_key(args, also=("GOOGLE_API_KEY",))
    if not key:
        raise SystemExit(
            f"error: set the {_key_env(args)} environment variable, pass --api-key-file, "
            "or use --provider fake"
        )
    return GeminiProvider(key, model or DEFAULT_MODEL)


def _read_srt(path: str) -> list[srt.Cue]:
    try:
        return srt.parse_file(Path(path))
    except srt.SrtError as exc:
        raise _Fail(str(exc)) from None
    except (OSError, ValueError) as exc:
        raise _Fail(f"{path}: {_describe(exc)}") from None


def _report(bundle: TranslationBundle, result: TranslationResult, findings) -> dict:
    kinds: dict[str, int] = {}
    for issue in result.issues:
        kinds[issue.kind] = kinds.get(issue.kind, 0) + 1
    return {
        "bundle_hash": bundle.bundle_hash(),
        "cues": len(result.cues),
        "calls": [
            {"kind": c.kind, "targets": c.targets, "tokens_in": c.tokens_in, "tokens_out": c.tokens_out,
             "model": c.model, "outcome": c.outcome}
            for c in result.calls
        ],
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "warnings": list(result.warnings),
        "issues": kinds,
        "review": [f.to_dict() for f in findings],
    }


# ---------------------------------------------------------------- commands


def _cmd_translate(args: argparse.Namespace) -> int:
    src = Path(args.file)
    cues = _read_srt(args.file)
    bundle = _bundle_for(args, cues)  # names the output (and checks the glossary) before anything costs money
    out = Path(args.out) if args.out else default_output_path(src, bundle.target_lang, bundle.source_lang or None)
    report_path = Path(args.report) if args.report else out.with_name(out.stem + ".report.json")
    if out.exists() and not args.force:
        print(f"error: {out} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    # The default report belongs to a run and replaces the last one; one named with --report is the user's own file.
    if args.report and report_path.exists() and not args.force:
        print(f"error: {report_path} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    provider = _build_provider(args, bundle.params.model or None, bundle.target_lang)

    def progress(done: int, total: int) -> None:
        print(f"\r{done}/{total} cues", end="", file=sys.stderr, flush=True)

    try:
        result = run(bundle, provider, on_progress=progress)
    except TranslationError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except ProviderError as exc:
        print(f"\nerror: provider failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(file=sys.stderr)

    try:
        srt.write_atomic(out, srt.serialize(result.cues))
    except OSError as exc:
        print(f"error: the translation could not be written to {out}: {_describe(exc)}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(
        f"wrote {out} ({len(result.cues)} cues, {len(result.calls)} call(s), "
        f"{result.tokens_in} tokens in / {result.tokens_out} out)",
        file=sys.stderr,
    )
    findings = review(list(bundle.cues), result.cues, bundle.glossary)
    try:
        srt.write_atomic(report_path, json.dumps(_report(bundle, result, findings), ensure_ascii=False, indent=2))
    except OSError as exc:
        print(f"error: the subtitle was written, but the report {report_path} could not be: {_describe(exc)}",
              file=sys.stderr)
        return 1
    print(f"report {report_path}", file=sys.stderr)
    return 0


def _cmd_bundle(args: argparse.Namespace) -> int:
    cues = _read_srt(args.file)
    bundle = _bundle_for(args, cues)
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"error: {out} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    try:
        srt.write_atomic(out, bundle.to_json())
    except OSError as exc:
        raise _Fail(f"{out}: {_describe(exc)}") from None
    print(f"wrote {out} (bundle {bundle.bundle_hash()[:12]}, {len(bundle.cues)} cues)", file=sys.stderr)
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    cues = _read_srt(args.file) if args.file else None
    bundle = _bundle_for(args, cues)
    requests = render_requests(bundle)  # no provider: an unset output limit is planned with the default one
    if not requests:
        print("(nothing to translate)")
        return 0
    for req in requests:
        if args.part is not None and req.part != args.part:
            continue
        first, last = req.targets[0], req.targets[-1]
        print(f"===== request {req.part + 1} of {len(requests)}: cues {first}-{last} ({len(req.targets)}) =====")
        print("----- system")
        print(req.system)
        print("----- user")
        print(req.user)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Review a translation the way the worker does: with the glossary narrowed to the episode (R11-4). A ``--bundle``
    brings its own, already narrowed; ``--glossary`` is narrowed here by ``--season``/``--episode``, or by the bundle's."""
    source = _read_srt(args.source)
    target = _read_srt(args.translation)
    base = _load_bundle(args.bundle)
    gloss = base.glossary if base is not None and not base.glossary.is_empty() else None
    if args.glossary:
        season = args.season if args.season is not None else (base.season if base is not None else None)
        episode = args.episode if args.episode is not None else (base.episode if base is not None else None)
        gloss = _load_glossary(args.glossary).for_episode(season, episode)
    elif (args.season is not None or args.episode is not None) and base is not None:
        raise _Fail("the bundle's glossary is already narrowed to its own episode; pass --glossary to narrow another")
    findings = review(source, target, gloss)
    if args.format == "json":
        print(json.dumps([f.to_dict() for f in findings], ensure_ascii=False, indent=2))
    else:
        for f in findings:
            where = "file" if f.cue == 0 else f"cue {f.cue}"
            print(f"{f.severity:7} {where:9} {f.kind:14} {f.detail}")
        print(f"{sum(1 for f in findings if f.severity == 'error')} error(s), "
              f"{sum(1 for f in findings if f.severity == 'warning')} warning(s)")
    return 1 if any(f.severity == "error" for f in findings) else 0


def _cmd_compare(args: argparse.Namespace) -> int:
    cues = _read_srt(args.file)
    golden = None
    if args.golden:
        golden = [c.text for c in _read_srt(args.golden)]
        if len(golden) != len(cues):
            raise _Fail(f"the golden file has {len(golden)} cues, the source has {len(cues)}")
    bundle_paths = args.bundle or []
    if not (1 <= len(bundle_paths) <= 2) or (len(bundle_paths) == 1 and golden is None):
        raise _Fail("compare needs two --bundle, or one --bundle with --golden")
    # Everything that can fail without a call (a bad bundle, a missing key) fails here, before the first paid run.
    plan = []
    for number, path in enumerate(bundle_paths, start=1):
        args.bundle = path
        bundle = _bundle_for(args, cues)
        provider = _build_provider(args, bundle.params.model or None, bundle.target_lang)
        name = Path(path).stem if len(bundle_paths) > 1 else "result"
        plan.append((f"{number}:{name}", bundle, provider))
    if args.provider != "fake":
        print(f"note: this makes {len(plan)} translation(s) of {len(cues)} cues, each billed by the provider",
              file=sys.stderr)
    summaries, results, failures = [], [], []
    for label, bundle, provider in plan:
        try:
            result = run(bundle, provider)
        except TranslationError as exc:
            failures.append(f"{label}: {exc}")
            continue
        except ProviderError as exc:
            failures.append(f"{label}: provider failed: {type(exc).__name__}: {exc}")
            continue
        findings = review(list(bundle.cues), result.cues, bundle.glossary)
        summaries.append(compare_mod.summarize(label, result, findings, bundle_hash=bundle.bundle_hash(), golden=golden))
        results.append(result)
    if summaries:  # what was paid for is shown even when the other side failed
        changed = compare_mod.changed_cues(results[0], results[1]) if len(results) == 2 else None
        print(compare_mod.format_table(summaries, changed))
    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)
    return 1 if failures else 0


def _cmd_memory(args: argparse.Namespace) -> int:
    """Read the story facts of several episodes (given in story order) and print what the memory would hold.

    For judging extraction quality on a series you know (ARCHITECTURE section 8): check each relationship
    event against the scene at the printed time, then the running state after each episode.
    """
    from .memory import UnitFacts, extract_facts, fold

    def _clock(ms: int) -> str:
        seconds = ms // 1000
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

    def _fake_facts(req, call_no) -> str:
        return json.dumps({"characters": [], "relationship_events": [], "terms": [], "summary": "(fake provider: no facts)"})

    if args.provider == "fake":
        provider = FakeProvider(handler=_fake_facts)
    else:
        # Same pacing and backoff as the worker: a busy model ("high demand") is retried, not fatal.
        from .providers.limiter import RateLimiter, ResilientProvider

        provider = ResilientProvider(_build_provider(args), RateLimiter(rpm=10), max_attempts=6, base_delay=10.0)
    folded: list[UnitFacts] = []
    saved: dict[str, dict] = {}
    for path in map(Path, args.files):
        try:
            cues = srt.parse_file(path)
        except srt.SrtError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except (OSError, ValueError) as exc:
            print(f"error: {path}: {_describe(exc)}", file=sys.stderr)
            return 2
        try:
            result = extract_facts(provider, cues, title=args.title or "", source_lang=args.source)
        except FactsError as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            return 1
        except ProviderError as exc:
            print(f"error: provider failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        facts = result.facts
        saved[path.name] = facts.to_dict()
        episode = re.search(r"S\d{1,3}E\d{1,4}", path.stem, re.IGNORECASE)
        folded.append(UnitFacts(episode.group(0).upper() if episode else path.stem, facts))
        print(f"== {path.name} ({len(cues)} cues, {result.tokens_in} tokens in / {result.tokens_out} out)")
        print(f"summary: {facts.summary}")
        if facts.characters:
            print("characters: " + "; ".join(
                f"{c.name}" + (f" [{c.gender}]" if c.gender else "") + (f" ({c.age_hint})" if c.age_hint else "")
                for c in facts.characters))
        for e in facts.relationship_events:
            when = _clock(cues[e.cue - 1].start_ms)
            detail = f" ({e.detail})" if e.detail else ""
            print(f'  cue {e.cue} @ {when}: {e.a} & {e.b}: {e.relation}{detail}, closeness {e.intimacy}/5, '
                  f'{e.hierarchy}, confidence {e.confidence}  "{e.evidence}"')
        if facts.terms:
            print("terms: " + ", ".join(t.source for t in facts.terms))
        state = fold(folded)
        if state.relationships:
            print("-- where each pair stands after this episode:")
            for r in state.relationships:
                detail = f" ({r.detail})" if r.detail else ""
                print(f"     {r.a} & {r.b}: {r.relation}{detail}, closeness {r.intimacy}/5, {r.hierarchy} (since {r.since})")
        print()
    if args.save:
        Path(args.save).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved the facts to {args.save}", file=sys.stderr)
    return 0


def _cmd_check_glossary(args: argparse.Namespace) -> int:
    try:
        g = glossary_mod.load(args.file)
    except glossary_mod.GlossaryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: {args.file}: {_describe(exc)}", file=sys.stderr)
        return 2
    print(
        f"ok: {len(g.terms)} terms ({len(g.locked_terms())} locked), "
        f"{len(g.characters)} characters, {len(g.address_rules)} address rules"
    )
    return 0


# ---------------------------------------------------------------- parser


def _context_flags(p: argparse.ArgumentParser, bundle_flag: bool = True) -> None:
    """Flags that describe the translation context. Defaults are None so a bundle's values survive unless given."""
    if bundle_flag:
        p.add_argument("--bundle", help="a bundle JSON (from `subcore bundle`); flags given here override it")
    p.add_argument("--glossary", help="glossary YAML (see docs/design/SPEC.md FR-4)")
    p.add_argument("--target", help="target language code (default: vi)")
    p.add_argument("--source", help="source language code, e.g. en")
    p.add_argument("--title", help="series/movie title (sent to the model as context)")
    p.add_argument("--overview", help="short plot overview (sent to the model as context)")
    p.add_argument("--season", type=int)
    p.add_argument("--episode", type=int)
    p.add_argument("--model", help="model name, recorded in the bundle (gemini default: gemini-3.1-flash-lite)")
    p.add_argument("--temperature", type=float)
    p.add_argument("--max-output-tokens", type=int, help="override the output limit used for planning")


_KEY_ENV_HELP = (
    "environment variable holding the API key (default: GEMINI_API_KEY for gemini, OPENAI_COMPAT_API_KEY for "
    "openai_compat; a key of one provider is never sent to another)"
)


def _provider_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--provider", choices=["gemini", "openai_compat", "fake"], default="gemini")
    p.add_argument("--base-url", help="openai_compat only: e.g. http://localhost:11434/v1 or https://openrouter.ai/api/v1")
    p.add_argument("--api-key", help="API key on the command line (prefer the env var or --api-key-file: flags show in ps)")
    p.add_argument("--api-key-file", help="file whose first non-empty line is the API key")
    p.add_argument("--api-key-env", help=_KEY_ENV_HELP)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subcore", description="Subtitle translation engine (manual use)")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("translate", help="translate one SRT file (or a bundle)")
    t.add_argument("file", help="source .srt file")
    _context_flags(t)
    _provider_flags(t)
    t.add_argument("--out", help="output path (default: <name>.<target>.srt beside the input)")
    t.add_argument("--force", action="store_true", help="overwrite the output file if it exists")
    t.add_argument("--report", help="report JSON path (default: <output>.report.json, replaced on every run)")
    t.set_defaults(func=_cmd_translate)

    b = sub.add_parser("bundle", help="write the bundle of a translation without calling a model")
    b.add_argument("file", help="source .srt file")
    _context_flags(b)
    b.add_argument("-o", "--out", required=True, help="bundle JSON to write")
    b.add_argument("--force", action="store_true")
    b.set_defaults(func=_cmd_bundle)

    r = sub.add_parser("render-prompt", help="print the requests a translation would send (no provider call)")
    r.add_argument("file", nargs="?", help="source .srt file (optional with --bundle)")
    _context_flags(r)
    r.add_argument("--part", type=int, help="only this request (0-based)")
    r.set_defaults(func=_cmd_render)

    v = sub.add_parser("validate", help="review a translation against its source")
    v.add_argument("source", help="source .srt")
    v.add_argument("translation", help="translated .srt")
    v.add_argument("--glossary", help="glossary YAML: locked terms are checked")
    v.add_argument("--bundle", help="a bundle JSON: its glossary (already narrowed to its episode) is used")
    v.add_argument("--season", type=int, help="narrow --glossary to this episode, as the worker does")
    v.add_argument("--episode", type=int)
    v.add_argument("--format", choices=["text", "json"], default="text")
    v.set_defaults(func=_cmd_validate)

    c = sub.add_parser("compare", help="run two bundles (or one against a golden set) and print the numbers")
    c.add_argument("file", help="source .srt file")
    c.add_argument("--bundle", action="append", help="a bundle JSON; give it twice to compare two")
    c.add_argument("--golden", help="reviewed .srt to compare the output against")
    _context_flags(c, bundle_flag=False)
    _provider_flags(c)
    c.set_defaults(func=_cmd_compare)

    m = sub.add_parser(
        "memory", help="print the story facts of several episodes (in story order) to judge extraction quality"
    )
    m.add_argument("files", nargs="+", help="source .srt files, earliest episode first")
    m.add_argument("--provider", choices=["gemini", "openai_compat", "fake"], default="gemini")
    m.add_argument("--model", help="model name (gemini default: gemini-3.1-flash-lite; required for openai_compat)")
    m.add_argument("--base-url", help="openai_compat only: e.g. http://localhost:11434/v1")
    m.add_argument("--api-key", help="API key on the command line (prefer the env var or --api-key-file)")
    m.add_argument("--api-key-file", help="file whose first non-empty line is the API key")
    m.add_argument("--api-key-env", help=_KEY_ENV_HELP)
    m.add_argument("--title", help="series title (sent to the model as context)")
    m.add_argument("--source", help="source language code, e.g. en")
    m.add_argument("--save", help="also write the facts of every file to this JSON file")
    m.set_defaults(func=_cmd_memory, target=_DEFAULT_TARGET)

    g = sub.add_parser("check-glossary", help="validate a glossary YAML file")
    g.add_argument("file")
    g.set_defaults(func=_cmd_check_glossary)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except _Fail as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
