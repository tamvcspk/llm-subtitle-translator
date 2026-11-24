"""Disk I/O tests for P18a: display reads, dry-run impact, gap rechecks and policy planning must not re-hash or
re-scan folders needlessly, while overwrite decisions still hash for real."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subcore.memory import EXTRACT_PROMPT_VERSION, Facts
from subcore.providers.fake import FakeProvider

from subserver.adapters.store import Store
from subserver.domain.ports import KIND_EPISODE, KIND_SERIES, CatalogEntry, CatalogSubtitle
from subserver.domain.translation import ContextGapError
from subserver.jobs.jobs import FILL
from subserver.jobs.queue import JobQueue
from subserver.services.glossaries import Glossaries
from subserver.services.job_requests import JobRequests, Selection
from subserver.services.library import Library
from subserver.services.memory import StoryMemory
from subserver.services.styles import Styles
from subserver.services.suggestions import Proposal, SuggestionService
from subserver.services.versions import TranslationVersions, sha256_file

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello world.\n\n"


class DiskIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(":memory:")
        self.glossaries = Glossaries(self.store)
        self.styles = Styles(self.store)
        self.versions = TranslationVersions(self.store, self.root / "backups")
        self.memory = StoryMemory(self.store, self.store, settings_source=lambda: {"memory_enabled": True})
        self.library = Library(self.store, self.glossaries, self.versions, memory=self.memory, styles=self.styles)
        self.suggestions = SuggestionService(self.store, self.library)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_series(self, episodes: int = 3) -> tuple[int, list[int], list[Path]]:
        series_id = self.store.upsert_series("bazarr-1", title="Malcolm", bazarr_id="1")
        unit_ids: list[int] = []
        outputs: list[Path] = []
        for ep in range(1, episodes + 1):
            src = self.root / f"Malcolm.S01E{ep:02d}.en.srt"
            out = self.root / f"Malcolm.S01E{ep:02d}.vi.srt"
            src.write_text(SRT, encoding="utf-8")
            uid = self.store.upsert_unit(
                series_id=series_id, season=1, episode=ep, title=f"Ep {ep}",
                media_type="series", bazarr_id=str(100 + ep),
                video_path=str(self.root / f"Malcolm.S01E{ep:02d}.mkv"),
                source_path=str(src), source_code="en", source_language="English",
            )
            self.store.set_output(uid, "vi", str(out), "Vietnamese")
            unit = self.store.get_unit(uid)
            rules_hash = self.library.unit_rules(unit, "vi").hash
            self.versions.publish(
                str(out), SRT, source_path=str(src), source_sha256=sha256_file(src),
                target_lang="vi", unit_id=uid, glossary_hash=rules_hash,
            )
            unit_ids.append(uid)
            outputs.append(out)
        return series_id, unit_ids, outputs

    def test_series_units_does_not_rehash_unchanged_files_on_second_read(self) -> None:
        series_id, _, outputs = self._seed_series(3)
        calls: list[Path] = []
        real_hash = sha256_file

        def counting_hash(path: Path) -> str:
            calls.append(Path(path))
            return real_hash(path)

        with mock.patch("subserver.services.versions.sha256_file", side_effect=counting_hash):
            first = self.library.series_units(series_id)
            self.assertEqual(len(first["units"]), 3)
            self.assertEqual(len(calls), 3)  # hashed once to populate stat cache
            calls.clear()
            second = self.library.series_units(series_id)
            self.assertEqual(len(second["units"]), 3)
            self.assertEqual(calls, [])  # stat unchanged -> zero file reads
            # Touching a file's content changes stat -> only that one file is re-hashed.
            outputs[0].write_text(SRT + "2\n00:00:03,000 --> 00:00:04,000\nEdited.\n\n", encoding="utf-8")
            third = self.library.series_units(series_id)
            self.assertEqual(calls, [outputs[0]])
            self.assertTrue(third["units"][0]["targets"][0]["modified_outside"])

    def test_impact_units_and_suggestion_dry_run_never_hash_files(self) -> None:
        series_id, _, _ = self._seed_series(4)
        self.suggestions.propose(
            series_id, "vi", [Proposal("term", {"source": "Lucky Aide", "target": "Lucky Aide"})], source="facts",
        )
        (pending,) = self.suggestions.listing(status="pending")["items"]

        with mock.patch("subserver.services.versions.sha256_file", side_effect=AssertionError("must not hash")):
            asked = self.suggestions.accept(pending["id"], dry_run=True)
        self.assertEqual(asked["would_stale"], 4)
        self.assertFalse(asked["saved"])

    def test_overwrite_path_still_hashes_for_real(self) -> None:
        _, _, outputs = self._seed_series(1)
        calls: list[Path] = []
        real_hash = sha256_file

        def counting_hash(path: Path) -> str:
            calls.append(Path(path))
            return real_hash(path)

        with mock.patch("subserver.services.versions.sha256_file", side_effect=counting_hash):
            # Populate stat cache via display call first
            self.assertTrue(self.versions.is_system_file(str(outputs[0]), trusting_stat=True))
            calls.clear()
            # Decision call leaves trusting_stat=False -> must hash for real
            self.assertTrue(self.versions.is_system_file(str(outputs[0])))
            self.assertEqual(calls, [outputs[0]])

    def test_memory_plan_fast_path_during_gap_wait_does_not_hash_prior_sources(self) -> None:
        series_id = self.store.upsert_series("bazarr-2", title="Gap Show", bazarr_id="2")
        # Ep 1..3 have source and cached facts; Ep 4 has NO source (gap); Ep 5 is the target episode.
        units = []
        for ep in range(1, 6):
            src = self.root / f"Show.S01E{ep:02d}.en.srt"
            has_src = ep != 4
            if has_src:
                src.write_text(SRT, encoding="utf-8")
            uid = self.store.upsert_unit(
                series_id=series_id, season=1, episode=ep, title=f"Ep {ep}",
                media_type="series", bazarr_id=str(200 + ep),
                video_path=str(self.root / f"Show.S01E{ep:02d}.mkv"),
                source_path=str(src) if has_src else "", source_code="en" if has_src else "",
            )
            if ep < 4:
                self.store.save_facts(
                    uid, sha256_file(src), EXTRACT_PROMPT_VERSION, model="fake", facts=Facts(summary=f"Ep {ep}"),
                )
            units.append(self.store.get_unit(uid))

        now = 10_000.0
        memory = StoryMemory(
            self.store, self.store,
            settings_source=lambda: {"memory_enabled": True, "memory_gap_wait_seconds": 1800},
            clock=lambda: now,
        )
        ep5 = units[4]
        job: dict = {"source_code": "en"}
        provider_for = lambda _task: FakeProvider(  # noqa: E731
            handler=lambda _req, _n: json.dumps({"summary": "Ep 5", "characters": [], "relationships": [], "terms": []})
        )

        # First plan() discovers the gap (Ep 4 missing) and hashes Ep 1..3 once.
        hashes: list[Path] = []
        real_hash = sha256_file
        with mock.patch("subserver.services.memory.sha256_file", side_effect=lambda p: (hashes.append(Path(p)), real_hash(p))[1]):
            with self.assertRaises(ContextGapError) as caught:
                memory.plan(ep5, job, {"memory_enabled": True, "memory_gap_wait_seconds": 1800}, provider_for)
        self.assertEqual(len(hashes), 3)
        job["gap_since"] = caught.exception.since
        job["missing_units"] = list(caught.exception.missing)
        job["missing_unit_ids"] = list(caught.exception.missing_ids)

        # 5 minutes later, Ep 4 is still missing -> fast-path only stats Ep 4, ZERO hashes!
        now += 300.0
        with mock.patch("subserver.services.memory.sha256_file", side_effect=AssertionError("must not hash while gap persists")):
            with self.assertRaises(ContextGapError):
                memory.plan(ep5, job, {"memory_enabled": True, "memory_gap_wait_seconds": 1800}, provider_for)

        # Even if full plan runs again after gap_wait expires, Ep 1..3 are cached by (mtime_ns, size) -> only Ep 5 hashes!
        now += 2000.0
        hashes.clear()
        with mock.patch("subserver.services.memory.sha256_file", side_effect=lambda p: (hashes.append(Path(p)), real_hash(p))[1]):
            plan = memory.plan(ep5, job, {"memory_enabled": True, "memory_gap_wait_seconds": 1800}, provider_for)
        self.assertFalse(plan.complete)
        self.assertEqual(hashes, [Path(ep5.source_path)])

    def test_job_requests_plan_caches_iterdir_per_season_folder(self) -> None:
        season_dir = self.root / "Season 01"
        season_dir.mkdir()
        series_entry = CatalogEntry(
            kind=KIND_SERIES, bazarr_id="10", series_key="bazarr-10", title="Show",
        )
        ep_entries = []
        for ep in range(1, 6):
            src = season_dir / f"Show.S01E{ep:02d}.en.srt"
            src.write_text(SRT, encoding="utf-8")
            ep_entries.append(CatalogEntry(
                kind=KIND_EPISODE, bazarr_id=str(300 + ep), series_key="bazarr-10", parent_bazarr_id="10",
                season=1, episode=ep, title=f"Ep {ep}", video_path=str(season_dir / f"Show.S01E{ep:02d}.mkv"),
                subtitles=(CatalogSubtitle(code="en", path=str(src)),), missing=("vi",),
            ))
        self.store.catalog_apply([series_entry, *ep_entries], at=1000.0)
        stored_series = self.store.catalog_entries(KIND_SERIES)[0]
        stored_eps = self.store.catalog_entries(KIND_EPISODE, parent_id=stored_series.id)

        cfg = {
            "source_languages": [{"code": "en", "language": "English", "enabled": True}],
            "target_languages": [{"code": "vi", "language": "Vietnamese", "enabled": True}],
        }
        queue = JobQueue(self.store)
        requests = JobRequests(
            self.store, self.library, queue, self.versions,
            config_source=lambda: cfg, settings_source=lambda: {},
        )
        iterdir_calls = 0
        real_iterdir = Path.iterdir

        def counting_iterdir(path_self: Path):
            nonlocal iterdir_calls
            if path_self == season_dir:
                iterdir_calls += 1
            return real_iterdir(path_self)

        with mock.patch.object(Path, "iterdir", counting_iterdir):
            plan = requests.plan(Selection(everything=True), FILL, automatic=True, entries=stored_eps, measure=False)
        self.assertEqual(len(plan.planned), 5)
        self.assertEqual(iterdir_calls, 1)  # 5 episodes in one folder -> 1 iterdir()


if __name__ == "__main__":
    unittest.main()
