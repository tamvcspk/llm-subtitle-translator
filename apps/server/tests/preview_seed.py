"""Fill an isolated state directory with a small, believable library so the console can be tried at once (no key, no
Bazarr, no network): ``scripts/preview.ps1`` runs this and then starts the worker on it.

    uv run --directory apps/server python -m tests.preview_seed --state-dir ../../local-state/preview [--reset]

It builds the same objects the worker builds and translates with a fake provider, so what the console shows is real
rows made by the real code: a series with two translated episodes and one missing, a glossary with a history, a preset
in use, a character card, a Spanish subtitle next to the Vietnamese one, and a movie. Not a test: nothing here runs in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from subcore.memory import EXTRACT_PROMPT_VERSION, Character, Facts, FactTerm
from subcore.providers import FakeProvider

from subserver.services.glossaries import Glossaries
from subserver.services.library import Library
from subserver.domain.ports import KIND_EPISODE, KIND_MOVIE, KIND_SERIES, CatalogEntry, CatalogSubtitle
from subserver.adapters.store import Store
from subserver.services.styles import Styles
from subserver.services.suggestions import Proposal, SuggestionService
from subserver.jobs.translation_attempt import TranslationAttempt
from subserver.services.versions import TranslationVersions

MARK = ".preview-seed"
SETTINGS = {"gst_model": "preview-fake"}
LINES = [
    "Did you bring the Kyber crystal?",
    "Not here. Someone is listening.",
    "You said you trusted me.",
    "I trust you. I do not trust the walls.",
    "Then tell me where we go.",
    "North, past the old observatory.",
    "That is three days on foot.",
    "Two, if we do not stop to argue.",
    "I never argue.",
    "You are arguing right now.",
    "Fine. Lead the way.",
    "Keep your voice down.",
]


# The lines of the one episode of "Edit Demo" (``--live`` only): three of them say "Mom" and the machine wrote "Mẹ" for it,
# so correcting "Mẹ" to another word in three cues is a swap the feedback loop can find without a model call.
EDIT_LINES = [
    "Mom, wait for me!",
    "I love you, Mom.",
    "Where is Mom going?",
    "The door is open.",
    "Dad is late again.",
]


def srt(episode: int, lines: list[str] = LINES) -> str:
    blocks = []
    for n, text in enumerate(lines, start=1):
        start, end = (n - 1) * 2500, (n - 1) * 2500 + 2000
        stamp = lambda ms: f"00:00:{ms // 1000:02d},{ms % 1000:03d}"  # noqa: E731
        blocks.append(f"{n}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    return "\n".join(blocks)


def translate(text: str) -> str:
    return f"[vi] {text}".replace("Kyber crystal", "tinh thể Kyber").replace("Mom", "Mẹ")


# What the stored facts of "Arcane Demo" say (``--facts``, always on in the preview): the terms research looks up are the ones two
# or more episodes share and the glossary does not have. "Old Observatory" is in all three episodes and "Hextech Gate" in two:
# those are the two to research. "Lone Pass" is in one only, and "Kyber crystal" is already in the glossary: neither is asked.
FACT_TERMS = {1: ("Old Observatory", "Lone Pass", "Kyber crystal"), 2: ("Old Observatory", "Hextech Gate", "Kyber crystal"),
              3: ("Old Observatory", "Hextech Gate", "Kyber crystal")}


def seed_facts(store: Store, show: Path) -> None:
    """Facts for the three episodes of "Arcane Demo", as if story memory had read them (no model call). Makes the units too, so
    this works when nothing is translated (live mode)."""
    series_id = store.upsert_series("bazarr-7", title="Arcane Demo", bazarr_id="7")
    for episode, terms in FACT_TERMS.items():
        source = show / f"Arcane.Demo.S01E{episode:02d}.en.srt"
        unit_id = store.upsert_unit(
            series_id=series_id, season=1, episode=episode, title=f"Episode {episode}", media_type="series",
            bazarr_id=str(100 + episode), video_path=str(show / f"Arcane.Demo.S01E{episode:02d}.mkv"),
            source_path=str(source), source_code="en",
        )
        facts = Facts(
            (Character("Zed", gender="m"), Character("Vi", gender="f")), (), tuple(FactTerm(t, "place") for t in terms),
            f"Episode {episode}: Zed and Vi head north.",
        )
        store.save_facts(
            unit_id, hashlib.sha256(source.read_bytes()).hexdigest(), EXTRACT_PROMPT_VERSION, model="preview-seed", facts=facts,
        )


EVIDENCE = [{"unit": 1, "cue": 5, "timecode": "00:00:10,000", "text": "Then tell me where we go."}]


def seed_review(store: Store, library: Library, glossaries: Glossaries, entries: list[CatalogEntry]) -> None:
    """What P18 changed on the Review, Suggestions-tab, Glossary and Characters screens needs rows the older seed lacked:
    seven pending cards on "Arcane Demo" (three terms, a pair with two stages, a plain pair, a change of an existing pair,
    a character) and a series "Forms Demo" whose glossary has adjacent identical forms of address to clean up."""
    series_id = store.upsert_series("bazarr-7", title="Arcane Demo", bazarr_id="7")
    service = SuggestionService(store, library, settings_source=lambda: {})
    ev = tuple(EVIDENCE)
    proposals = [
        Proposal("term", {"source": "Old Observatory", "target": "Đài quan sát cũ"}, evidence=ev),
        Proposal("term", {"source": "Hextech Gate", "target": "Cổng Hextech"}, evidence=ev),
        Proposal("term", {"source": "Lone Pass", "target": "Đèo Cô Độc"}, evidence=ev),
        Proposal("address_rule", {
            "a": "Zed", "b": "Vi", "stages": [
                {"a_to_b": "anh–em", "b_to_a": "em–anh"},
                {"from": "S01E03", "a_to_b": "tôi–em", "b_to_a": "em–anh"},
            ],
        }, evidence=ev),
        Proposal("address_rule", {"a": "Zed", "b": "Mira", "a_to_b": "tớ–cậu", "b_to_a": "cậu–tớ"}, evidence=ev),
        # The glossary already has Kyber-Zed (`tôi–anh`): this one proposes a change of that pair from S01E03.
        Proposal("address_rule", {
            "a": "Kyber", "b": "Zed", "change_from": "S01E03", "stages": [
                {"a_to_b": "tôi–anh", "b_to_a": "anh–tôi"},
                {"from": "S01E03", "a_to_b": "tao–mày", "b_to_a": "mày–tao"},
            ],
        }, evidence=ev),
        Proposal("character", {"name": "Mira", "target": "Mira", "gender": "f"}, evidence=ev),
    ]
    service.propose(series_id, "vi", proposals, source="facts", auto_accept=False)

    store.upsert_series("bazarr-13", title="Forms Demo", bazarr_id="13")
    forms = [
        "terms:",
        "  - {source: Old Observatory, target: Đài quan sát cũ}",
        "  - {source: Hextech Gate, target: Cổng Hextech}",
        "  - {source: Lone Pass, target: Đèo Cô Độc, locked: true}",
        "characters:",
        "  - {name: Zed, target: Zed, gender: m}",
        "  - {name: Vi, target: Vi, gender: f}",
        "address_rules:",
        "  - {a: Zed, b: Vi, a_to_b: 'anh–em', b_to_a: 'em–anh', from: S01E01}",
        "  - {a: Zed, b: Vi, a_to_b: 'anh–em', b_to_a: 'em–anh', from: S01E04}",
        "  - {a: Zed, b: Vi, a_to_b: 'anh–em', b_to_a: 'em–anh', from: S01E07}",
        "  - {a: Vi, b: Zed, a_to_b: 'em–anh', b_to_a: 'anh–em', from: S01E09}",
        "  - {a: Mira, b: Kyber, a_to_b: 'tớ–cậu', b_to_a: 'cậu–tớ', from: S01E01, until: S01E04}",
        "  - {a: Mira, b: Kyber, a_to_b: 'tôi–anh', b_to_a: 'anh–tôi', from: S01E05}",
        # R18-7: the same words across a gap (until S01E04, then from S01E07) must stay two rules after Clean up...
        "  - {a: Ekko, b: Vi, a_to_b: 'tớ–cậu', b_to_a: 'cậu–tớ', until: S01E04}",
        "  - {a: Ekko, b: Vi, a_to_b: 'tớ–cậu', b_to_a: 'cậu–tớ', from: S01E07}",
        # ...and two identical rules that start in season 2 merge into one that still starts at S02E01.
        "  - {a: Jinx, b: Ekko, a_to_b: 'tao–mày', b_to_a: 'mày–tao', from: S02E01}",
        "  - {a: Jinx, b: Ekko, a_to_b: 'tao–mày', b_to_a: 'mày–tao', from: S02E05}",
    ]
    glossaries.write("series", "bazarr-13", "vi", "\n".join(forms) + "\n", note="import")
    entries.append(CatalogEntry(KIND_SERIES, "13", series_key="bazarr-13", title="Forms Demo"))


def write_old_queue(state: Path) -> None:
    """What an older worker (before P10) and Bazarr's script leave in ``<state>/queue``, so the start-up has something
    to settle: a refusal of episode 3 (becomes Ignored) and a file the script dropped for the movie that nobody approved
    (stays Missing, not a job). Call it after ``seed``."""
    show = state / "preview-media" / "Arcane Demo" / "Season 1"
    queue = state / "queue"
    episode = {
        "media_type": "series", "series_id": "7", "media_id": "103", "series_title": "Arcane Demo", "season": 1,
        "episode": 3, "video_path": str(show / "Arcane.Demo.S01E03.mkv"),
        "subtitle_path": str(show / "Arcane.Demo.S01E03.en.srt"), "output_path": str(show / "Arcane.Demo.S01E03.vi.srt"),
        "source_code": "en", "target_code": "vi", "target_language": "Vietnamese", "created_at": time.time() - 3600,
    }
    movie_dir = state / "preview-media" / "Demo Movie (2020)"
    movie = {
        "media_type": "movie", "media_id": "55", "movie_title": "Demo Movie",
        "video_path": str(movie_dir / "Demo.Movie.mkv"), "subtitle_path": str(movie_dir / "Demo.Movie.en.srt"),
        "output_path": str(movie_dir / "Demo.Movie.vi.srt"), "source_code": "en", "target_code": "vi",
        "target_language": "Vietnamese", "created_at": time.time() - 3600,
    }
    files = {
        "rejected/old-refusal-s01e03.json": {**episode, "job_id": "old-refusal-s01e03"},
        "pending/old-script-movie.json": {**movie, "job_id": "old-script-movie"},
    }
    for name, job in files.items():
        path = queue / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(job), encoding="utf-8")


def seed(
    state: Path, translated: tuple[int, ...] = (1, 2), *, edit_demo: bool = False, candidate: bool = False, facts: bool = False,
    review: bool = False, bootstrap_demo: bool = False,
) -> str:
    media = state / "preview-media"
    show = media / "Arcane Demo" / "Season 1"
    show.mkdir(parents=True)
    store = Store(state / "app.db")
    versions = TranslationVersions(store, state / "backups" / "subtitles")
    glossaries, styles = Glossaries(store), Styles(store)
    styles.seed_presets()
    library = Library(store, glossaries, versions, styles=styles)
    fake = FakeProvider(handler=lambda req, call_no: [{"i": i, "text": translate(req.meta["source"][i])} for i in req.meta["targets"]])
    attempt = TranslationAttempt(versions=versions, usage=store, library=library, provider_factory=lambda s, m, **kw: fake)

    # A glossary with a history, so History and Restore have something to show; a preset in use; a character card.
    # They come first: the two episodes are then translated with them and are current, so a change shows a real count.
    glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Old term, target: Thuật ngữ cũ}\n", note="import")
    glossaries.write(
        "series", "bazarr-7", "vi",
        "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n"
        "address_rules:\n  - {a: Kyber, b: Zed, a_to_b: 'tôi–anh', b_to_a: 'anh–tôi'}\n",
        note="console",
    )
    styles.save_owner_style("series", "bazarr-7", "drama", {"tone": "grave"})
    styles.save_card("series", "bazarr-7", {"name": "Zed", "speech": "short, dry sentences"})
    entries = [CatalogEntry(KIND_SERIES, "7", series_key="bazarr-7", title="Arcane Demo", missing=("vi",))]
    for episode in (1, 2, 3):
        source = show / f"Arcane.Demo.S01E{episode:02d}.en.srt"
        source.write_text(srt(episode), encoding="utf-8")
        target = show / f"Arcane.Demo.S01E{episode:02d}.vi.srt"
        subtitles = [CatalogSubtitle("en", str(source))]
        if episode in translated:
            attempt.run({
                "job_id": f"seed-{episode}", "provider": "gemini-console", "media_type": "series", "series_id": "7",
                "media_id": str(100 + episode), "video_path": str(show / f"Arcane.Demo.S01E{episode:02d}.mkv"),
                "subtitle_path": str(source), "output_path": str(target), "source_code": "en", "target_code": "vi",
                "target_language": "Vietnamese", "series_title": "Arcane Demo", "season": 1, "episode": episode,
            }, "", SETTINGS)
            subtitles.append(CatalogSubtitle("vi", str(target)))
        entries.append(CatalogEntry(
            KIND_EPISODE, str(100 + episode), series_key="bazarr-7", parent_bazarr_id="7", season=1, episode=episode,
            title=f"Episode {episode}", video_path=str(show / f"Arcane.Demo.S01E{episode:02d}.mkv"),
            subtitles=tuple(subtitles), missing=() if episode in translated else ("vi",),
        ))

    if candidate and 1 in translated:
        # A variant of episode 1 in another voice, kept as a candidate (it never goes on disk): the contract test of the API
        # needs one to compare, judge and choose. Not part of the preview: its text differs from the translation in every cue.
        warm = FakeProvider(handler=lambda req, call_no: [{"i": i, "text": translate(req.meta["source"][i]) + " (warm)"} for i in req.meta["targets"]])
        variant = TranslationAttempt(versions=versions, usage=store, library=library, provider_factory=lambda s, m, **kw: warm)
        source = show / "Arcane.Demo.S01E01.en.srt"
        variant.run({
            "job_id": "seed-variant-1", "provider": "gemini-console", "media_type": "series", "series_id": "7",
            "media_id": "101", "video_path": str(show / "Arcane.Demo.S01E01.mkv"), "subtitle_path": str(source),
            "output_path": str(show / "Arcane.Demo.S01E01.vi.srt"), "source_code": "en", "target_code": "vi",
            "target_language": "Vietnamese", "series_title": "Arcane Demo", "season": 1, "episode": 1,
            "trigger": "variant", "variant": {"label": "warmer", "preset": "", "style": {"tone": "warm"}},
        }, "", SETTINGS)

    # A second series that has a glossary of its own: something for "Copy from another series" to copy.
    store.upsert_series("bazarr-9", title="Second Demo", bazarr_id="9")
    glossaries.write("series", "bazarr-9", "vi", "terms:\n  - {source: Hextech, target: Hextech, locked: true}\n", note="import")
    entries.append(CatalogEntry(KIND_SERIES, "9", series_key="bazarr-9", title="Second Demo"))

    movie_source = media / "Demo Movie (2020)" / "Demo.Movie.en.srt"
    movie_source.parent.mkdir(parents=True)
    movie_source.write_text(srt(1), encoding="utf-8")
    entries.append(CatalogEntry(
        KIND_MOVIE, "55", series_key="bazarr-movie-55", title="Demo Movie", video_path=str(movie_source.with_suffix(".mkv")),
        subtitles=(CatalogSubtitle("en", str(movie_source)),), missing=("vi",),
    ))
    if edit_demo:
        # One small episode translated by the fake provider (free, even in live mode), to correct by hand: the feedback loop
        # and the golden set need cues whose machine text is known, and a live translation is not.
        folder = media / "Edit Demo" / "Season 1"
        folder.mkdir(parents=True)
        source = folder / "Edit.Demo.S01E01.en.srt"
        source.write_text(srt(1, EDIT_LINES), encoding="utf-8")
        target = folder / "Edit.Demo.S01E01.vi.srt"
        attempt.run({
            "job_id": "seed-edit", "provider": "gemini-console", "media_type": "series", "series_id": "11", "media_id": "111",
            "video_path": str(folder / "Edit.Demo.S01E01.mkv"), "subtitle_path": str(source), "output_path": str(target),
            "source_code": "en", "target_code": "vi", "target_language": "Vietnamese", "series_title": "Edit Demo",
            "season": 1, "episode": 1,
        }, "", SETTINGS)
        entries.append(CatalogEntry(KIND_SERIES, "11", series_key="bazarr-11", title="Edit Demo"))
        entries.append(CatalogEntry(
            KIND_EPISODE, "111", series_key="bazarr-11", parent_bazarr_id="11", season=1, episode=1, title="Episode 1",
            video_path=str(folder / "Edit.Demo.S01E01.mkv"),
            subtitles=(CatalogSubtitle("en", str(source)), CatalogSubtitle("vi", str(target))),
        ))
    if bootstrap_demo:
        # One series with auto_request=on, an empty glossary and 3 missing episodes: lets Bootstrap hold & Hold again run in preview.
        bdir = media / "Bootstrap Demo" / "Season 1"
        bdir.mkdir(parents=True)
        bsid = store.upsert_series("bazarr-12", title="Bootstrap Demo", bazarr_id="12")
        store.set_series_auto_request(bsid, "on")
        entries.append(CatalogEntry(KIND_SERIES, "12", series_key="bazarr-12", title="Bootstrap Demo", missing=("vi",)))
        for ep in (1, 2, 3):
            src = bdir / f"Bootstrap.Demo.S01E{ep:02d}.en.srt"
            src.write_text(srt(ep), encoding="utf-8")
            entries.append(CatalogEntry(
                KIND_EPISODE, str(200 + ep), series_key="bazarr-12", parent_bazarr_id="12", season=1, episode=ep,
                title=f"Episode {ep}", video_path=str(bdir / f"Bootstrap.Demo.S01E{ep:02d}.mkv"),
                subtitles=(CatalogSubtitle("en", str(src)),), missing=("vi",),
            ))
    if facts:
        seed_facts(store, show)
    if review:
        seed_review(store, library, glossaries, entries)
    store.catalog_apply(entries, time.time())

    store.close()
    return str(state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--live", action="store_true", help="translate nothing here: a real model will, so every episode is missing")
    parser.add_argument("--old-queue", action="store_true", help="also leave an older worker's queue folders for the start-up to settle")
    parser.add_argument("--reset", action="store_true", help="delete the state directory first (only one this script made)")
    args = parser.parse_args()
    state = Path(args.state_dir).resolve()
    if state.exists():
        if not args.reset:
            print(f"{state} exists; keeping it (use --reset to rebuild it)")
            return 0
        if not (state / MARK).is_file():
            print(f"refusing to delete {state}: it has no {MARK} file, so this script did not make it", file=sys.stderr)
            return 2
        shutil.rmtree(state)
    state.mkdir(parents=True)
    (state / MARK).write_text("made by tests/preview_seed.py; --reset may delete this folder\n", encoding="utf-8")
    seed(state, () if args.live else (1, 2), edit_demo=args.live, facts=True, review=True, bootstrap_demo=not args.live)
    if args.old_queue:
        write_old_queue(state)
    print(f"seeded {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
