"""Manual translation and debugging: ``subcore translate file.srt --glossary g.yaml``."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from . import glossary as glossary_mod
from . import srt
from .providers import FakeProvider
from .providers.base import Provider, ProviderError
from .translate import PromptContext, TranslateOptions, TranslationError, translate

_LANG_SUFFIX_RE = re.compile(r"\.[A-Za-z]{2,3}$")


def default_output_path(src: Path, target: str, source_lang: str | None) -> Path:
    """``Movie.en.srt`` → ``Movie.vi.srt`` (appends the language if there is none to replace)."""
    stem = src.stem
    if source_lang and stem.lower().endswith(f".{source_lang.lower()}"):
        stem = stem[: -(len(source_lang) + 1)]
    elif not source_lang and _LANG_SUFFIX_RE.search(stem):
        stem = _LANG_SUFFIX_RE.sub("", stem)
    return src.with_name(f"{stem}.{target}.srt")


def _build_provider(args: argparse.Namespace) -> Provider:
    if args.provider == "fake":
        return FakeProvider(prefix=f"[{args.target}] ")
    from .providers.gemini import DEFAULT_MODEL, GeminiProvider

    key = ""
    for name in (args.api_key_env, "GOOGLE_API_KEY"):
        key = os.environ.get(name, "")
        if key:
            break
    if not key:
        raise SystemExit(f"error: set the {args.api_key_env} environment variable (or use --provider fake)")
    return GeminiProvider(key, args.model or DEFAULT_MODEL)


def _cmd_translate(args: argparse.Namespace) -> int:
    src = Path(args.file)
    try:
        cues = srt.parse_file(src)
        gloss = glossary_mod.load(args.glossary) if args.glossary else None
    except (OSError, srt.SrtError, glossary_mod.GlossaryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else default_output_path(src, args.target, args.source)
    if out.exists() and not args.force:
        print(f"error: {out} already exists (use --force to overwrite)", file=sys.stderr)
        return 2

    provider = _build_provider(args)
    ctx = PromptContext(
        target_lang=args.target,
        source_lang=args.source,
        glossary=gloss,
        season=args.season,
        episode=args.episode,
        title=args.title or "",
        overview=args.overview or "",
    )

    def progress(done: int, total: int) -> None:
        print(f"\r{done}/{total} cues", end="", file=sys.stderr, flush=True)

    try:
        result = translate(provider, cues, ctx, TranslateOptions(), on_progress=progress)
    except TranslationError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except ProviderError as exc:
        print(f"\nerror: provider failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(file=sys.stderr)

    srt.write_atomic(out, srt.serialize(result.cues))
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(
        f"wrote {out} ({len(result.cues)} cues, {len(result.calls)} call(s), "
        f"{result.tokens_in} tokens in / {result.tokens_out} out)",
        file=sys.stderr,
    )
    return 0


def _cmd_check_glossary(args: argparse.Namespace) -> int:
    try:
        g = glossary_mod.load(args.file)
    except (OSError, glossary_mod.GlossaryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"ok: {len(g.terms)} terms ({len(g.locked_terms())} locked), "
        f"{len(g.characters)} characters, {len(g.address_rules)} address rules"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subcore", description="Subtitle translation engine (manual use)")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("translate", help="translate one SRT file")
    t.add_argument("file", help="source .srt file")
    t.add_argument("--glossary", help="glossary YAML (see docs/design/SPEC.md FR-4)")
    t.add_argument("--target", default="vi", help="target language code (default: vi)")
    t.add_argument("--source", help="source language code, e.g. en")
    t.add_argument("--out", help="output path (default: <name>.<target>.srt beside the input)")
    t.add_argument("--force", action="store_true", help="overwrite the output file if it exists")
    t.add_argument("--provider", choices=["gemini", "fake"], default="gemini")
    t.add_argument("--model", help="model name (gemini default: gemini-3.5-flash)")
    t.add_argument("--api-key-env", default="GEMINI_API_KEY", help="environment variable holding the API key")
    t.add_argument("--title", help="series/movie title (sent to the model as context)")
    t.add_argument("--overview", help="short plot overview (sent to the model as context)")
    t.add_argument("--season", type=int)
    t.add_argument("--episode", type=int)
    t.set_defaults(func=_cmd_translate)

    g = sub.add_parser("check-glossary", help="validate a glossary YAML file")
    g.add_argument("file")
    g.set_defaults(func=_cmd_check_glossary)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
