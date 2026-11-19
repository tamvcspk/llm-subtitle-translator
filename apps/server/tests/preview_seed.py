"""Fill an isolated state directory with a small, believable library so the console can be tried at once (no key, no
Bazarr, no network): ``scripts/preview.ps1`` runs this and then starts the worker on it.

    uv run --directory apps/server python -m tests.preview_seed --state-dir ../../local-state/preview [--reset]

It builds the same objects the worker builds and translates with a fake provider, so what the console shows is real
rows made by the real code: a series with two translated episodes and one missing, a glossary with a history, a preset
in use, a character card, a Spanish subtitle next to the Vietnamese one, and a movie. Not a test: nothing here runs in CI.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from subcore.providers import FakeProvider

from subserver.glossaries import Glossaries
from subserver.library import Library
from subserver.ports import KIND_EPISODE, KIND_MOVIE, KIND_SERIES, CatalogEntry, CatalogSubtitle
from subserver.store import Store
from subserver.styles import Styles
from subserver.translation_attempt import TranslationAttempt
from subserver.versions import TranslationVersions

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


def srt(episode: int) -> str:
    blocks = []
    for n, text in enumerate(LINES, start=1):
        start, end = (n - 1) * 2500, (n - 1) * 2500 + 2000
        stamp = lambda ms: f"00:00:{ms // 1000:02d},{ms % 1000:03d}"  # noqa: E731
        blocks.append(f"{n}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    return "\n".join(blocks)


def translate(text: str) -> str:
    return f"[vi] {text}".replace("Kyber crystal", "tinh thể Kyber")


def seed(state: Path, translated: tuple[int, ...] = (1, 2)) -> str:
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
    store.catalog_apply(entries, time.time())

    store.close()
    return str(state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--live", action="store_true", help="translate nothing here: a real model will, so every episode is missing")
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
    seed(state, () if args.live else (1, 2))
    print(f"seeded {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
