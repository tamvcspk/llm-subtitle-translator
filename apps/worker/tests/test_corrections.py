"""P3: library, corrections, ranged retranslation, history and glossary (SPEC FR-10/FR-11, criteria 7 and 8).

Every provider is a fake; nothing touches the network.
"""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from subcore import srt
from subcore.providers import FakeProvider

from gst_worker import library_api
from gst_worker import queue as gst_queue
from gst_worker.glossaries import GlossaryFiles
from gst_worker.library import Library, identify_job
from gst_worker.store import _MIGRATIONS, Store
from gst_worker.translation_attempt import TranslationAttempt
from gst_worker.units import UnitService, parse_time_ms
from gst_worker.versions import TranslationVersions

SETTINGS = {"gst_model": "m-1"}
CUES = 12


def source_text(episode):
    blocks = []
    for n in range(1, CUES + 1):
        start, end = (n - 1) * 2000, (n - 1) * 2000 + 1500
        text = "The Kyber crystal glows" if n == 5 else f"Line {n} of episode {episode}"
        stamp = lambda ms: f"00:00:{ms // 1000:02d},{ms % 1000:03d}"  # noqa: E731
        blocks.append(f"{n}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    return "\n".join(blocks)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Env:
    def __init__(self, test):
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.media = self.root / "media" / "Show" / "Season 1"
        self.media.mkdir(parents=True)
        self.store = Store(self.data / "app.db")
        test.addCleanup(self.store.close)
        self.versions = TranslationVersions(self.store, self.data / "backups" / "subtitles")
        self.glossaries = GlossaryFiles(self.data)
        self.library = Library(self.store, self.glossaries, self.versions)
        self.prefix = "[vi] "
        self.fake = FakeProvider(handler=self.handler)
        self.attempt = TranslationAttempt(
            versions=self.versions, library=self.library, provider_factory=lambda settings, model, **kw: self.fake
        )
        self.queue_dir = str(self.root / "queue")
        self.refreshed = []
        self.service = UnitService(
            self.library, self.glossaries, self.versions, self.queue_dir, refresh_bazarr=self.refreshed.append
        )

    def handler(self, req, call_no):
        return [
            {"i": i, "text": self.prefix + req.meta["source"][i].replace("Kyber crystal", "tinh thể Kyber")}
            for i in req.meta["targets"]
        ]

    def job(self, episode, **extra):
        source = self.media / f"Show.S01E{episode:02d}.en.srt"
        if not source.exists():
            source.write_text(source_text(episode), encoding="utf-8")
        return {
            "job_id": f"auto-{episode}", "provider": "gemini-console", "media_type": "series",
            "series_id": "7", "media_id": str(100 + episode),
            "video_path": str(self.media / f"Show.S01E{episode:02d}.mkv"), "subtitle_path": str(source),
            "output_path": str(self.media / f"Show.S01E{episode:02d}.vi.srt"),
            "source_code": "en", "target_code": "vi", "target_language": "Vietnamese", **extra,
        }

    def translate(self, episode, **extra):
        return self.attempt.run(self.job(episode, **extra), "", SETTINGS)

    def unit(self, episode):
        series = self.store.list_series()[0]
        return next(u for u in self.store.list_units(series["id"]) if u["episode"] == episode)["id"]

    def output(self, episode):
        return str(self.media / f"Show.S01E{episode:02d}.vi.srt")

    def cue_texts(self, episode):
        return [c.text for c in srt.parse_file(self.output(episode))]

    def run_pending_retranslation(self):
        """Run the queued retranslation job the way the worker would (minus HTTP and TMDB)."""
        paths = sorted((Path(self.queue_dir) / "pending").glob("*.json"))
        self.test_job = json.loads(paths[-1].read_text(encoding="utf-8"))
        paths[-1].unlink()
        return self.attempt.run(self.test_job, "", SETTINGS)


class IdentityTests(unittest.TestCase):
    def test_identifies_series_episodes_from_bazarr_ids_and_file_names(self):
        job = {"media_type": "series", "series_id": "7", "video_path": "/media/Show Name (2019)/Season 2/Show.Name.S02E05.1080p.mkv"}
        ident = identify_job(job)
        self.assertEqual((ident.series_key, ident.season, ident.episode), ("bazarr-7", 2, 5))
        self.assertEqual(ident.series_title, "Show Name")

    def test_tvdb_id_makes_the_key_stable_across_bazarr_installs(self):
        ident = identify_job({"media_type": "series", "series_id": "7", "tvdb_id": "70327", "season": 1, "episode": 1,
                              "video_path": "/m/x.mkv", "series_title": "Malcolm in the Middle"})
        self.assertEqual((ident.series_key, ident.series_title), ("tvdb-70327", "Malcolm in the Middle"))

    def test_other_episode_notations_and_folder_title_fallback(self):
        ident = identify_job({"media_type": "series", "video_path": "/media/The Wire/Season 3/3x07 - Back Burners.mkv"})
        self.assertEqual((ident.season, ident.episode, ident.series_title), (3, 7, "The Wire"))
        self.assertEqual(ident.series_key, "title-the-wire")

    def test_movies_and_unparseable_files_are_movie_units(self):
        self.assertEqual(identify_job({"media_type": "movie", "media_id": "5", "video_path": "/m/Heat (1995).mkv"}).movie_key,
                         "bazarr-movie-5")
        self.assertEqual(identify_job({"video_path": "/m/Heat {tmdb-949}.mkv"}).movie_key, "tmdb-949")
        ident = identify_job({"media_type": "series", "video_path": "/m/Some Special.mkv"})
        self.assertIsNone(ident.series_key)
        self.assertTrue(ident.movie_key.startswith("path-"))


class StoreMigrationTests(unittest.TestCase):
    def test_a_p2_database_upgrades_in_place_and_keeps_its_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.db"
            old = sqlite3.connect(path)
            for statement in _MIGRATIONS[0].split(";"):
                if statement.strip():
                    old.execute(statement)
            old.execute("PRAGMA user_version = 1")
            old.execute(
                "INSERT INTO translations (output_path, version, stored_path, sha256, trigger, status, created_at)"
                " VALUES ('/m/a.vi.srt', 1, '/b/v1', 'abc', 'auto', 'current', 1.0)"
            )
            old.commit()
            old.close()
            store = Store(path)
            try:
                self.assertEqual(store.schema_version, 4)
                row = store.current_version("/m/a.vi.srt")
                self.assertEqual((row["sha256"], row["unit_id"], row["glossary_hash"]), ("abc", None, ""))
            finally:
                store.close()


class RegistrationTests(unittest.TestCase):
    def test_a_translated_job_registers_its_series_unit_and_output(self):
        env = Env(self)
        env.translate(1)
        series = env.store.list_series()
        self.assertEqual([(s["key"], s["title"], s["bazarr_id"]) for s in series], [("bazarr-7", "Show", "7")])
        unit = env.store.get_unit(env.unit(1))
        self.assertEqual((unit["season"], unit["episode"], unit["bazarr_id"]), (1, 1, "101"))
        self.assertEqual(env.store.outputs(unit["id"])[0]["output_path"], env.output(1))
        self.assertEqual(env.store.current_version(env.output(1))["unit_id"], unit["id"])

    def test_a_skipped_foreign_target_is_still_listed_without_being_touched(self):
        env = Env(self)
        Path(env.output(1)).write_text("foreign", encoding="utf-8")
        self.assertEqual(env.translate(1), "skipped-existing-output")
        listed = env.library.series_units(env.store.list_series()[0]["id"])["units"][0]
        self.assertEqual(listed["targets"][0]["status"], "untranslated")
        self.assertTrue(listed["targets"][0]["modified_outside"])
        self.assertEqual(Path(env.output(1)).read_text(encoding="utf-8"), "foreign")


class Criterion7Tests(unittest.TestCase):
    """SPEC section 6, criterion 7."""

    def test_cue_edit_then_whole_episode_retranslation_keeps_that_cue_byte_for_byte(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        edited = "Câu mười đã sửa tay\nhai dòng"

        result = env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 10, "text": edited}})
        self.assertEqual(result["version"], 2)  # applied at once, no model call
        self.assertEqual(len(env.fake.calls), 1)
        self.assertEqual(env.cue_texts(1)[9], edited)
        self.assertEqual(len(env.refreshed), 1)  # Bazarr is asked to rescan
        self.assertEqual(env.refreshed[0]["media_id"], "101")

        env.prefix = "[v2] "
        env.service.retranslate(unit, {"lang": "vi"})
        self.assertEqual(env.run_pending_retranslation(), "translated")

        retranslation = env.fake.calls[-1]
        self.assertEqual(retranslation.meta["targets"], [n for n in range(1, CUES + 1) if n != 10])
        self.assertIn("ALREADY TRANSLATED", retranslation.user)  # the locked cue is a sample for the model
        self.assertIn("Câu mười đã sửa tay", retranslation.user)
        texts = env.cue_texts(1)
        self.assertEqual(texts[9], edited)
        self.assertEqual(texts[0], "[v2] Line 1 of episode 1")
        block = srt.serialize([c for c in srt.parse_file(env.output(1)) if c.index == 10])
        self.assertEqual(block, f"10\n00:00:18,000 --> 00:00:19,500\n{edited}\n")

    def test_forward_term_reaches_later_episodes_only(self):
        env = Env(self)
        env.translate(3)  # the episode the person is looking at
        env.service.add_correction(env.unit(3), {
            "lang": "vi", "kind": "term", "scope": "forward",
            "payload": {"source": "Kyber crystal", "target": "tinh thể Kyber"},
        })
        yaml_text = (env.data / "series" / "bazarr-7" / "glossary.vi.yaml").read_text(encoding="utf-8")
        self.assertIn("from: S01E03", yaml_text)

        env.fake.calls.clear()
        env.translate(2)
        env.translate(4)
        episode2, episode4 = env.fake.calls[0], env.fake.calls[1]
        self.assertNotIn("Kyber crystal", episode2.system)
        self.assertNotIn("tinh thể Kyber", episode2.system)
        self.assertIn('"Kyber crystal"', episode4.system)
        self.assertIn("[LOCKED]", episode4.system)

    def test_series_scope_applies_everywhere_and_forward_marks_later_translations_stale(self):
        env = Env(self)
        for episode in (2, 3, 4):
            env.translate(episode)
        series_id = env.store.list_series()[0]["id"]
        self.assertEqual(env.library.series_list()[0]["counts"]["current"], 3)

        result = env.service.add_correction(env.unit(3), {
            "lang": "vi", "kind": "term", "scope": "forward",
            "payload": {"source": "Kyber crystal", "target": "tinh thể Kyber"},
        })
        self.assertEqual(result["impact"]["would_stale"], 2)  # episodes 3 and 4, not 2
        by_episode = {u["episode"]: u["status"] for u in env.library.series_units(series_id)["units"]}
        self.assertEqual(by_episode, {2: "current", 3: "stale", 4: "stale"})

        env.service.add_correction(env.unit(2), {
            "lang": "vi", "kind": "address", "scope": "series",
            "payload": {"a": "Alice", "b": "Bob", "a_to_b": "em–anh"},
        })
        env.fake.calls.clear()
        env.translate(5)
        self.assertIn("Alice speaking to Bob", env.fake.calls[0].system)


class EpisodeCorrectionTests(unittest.TestCase):
    def test_episode_term_and_note_shape_only_the_next_retranslation_and_flag_the_translation_stale(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        env.service.add_correction(unit, {"lang": "vi", "kind": "term", "payload": {"source": "Kyber crystal", "target": "tinh thể Kyber"}})
        env.service.add_correction(unit, {"lang": "vi", "kind": "note", "payload": {"text": "Bob is being sarcastic"}})
        self.assertEqual(env.library.unit_summary(env.store.get_unit(unit))["status"], "stale")
        self.assertFalse((env.data / "series").exists())  # episode scope never writes the glossary

        env.service.retranslate(unit, {"lang": "vi"})
        env.run_pending_retranslation()
        system = env.fake.calls[-1].system
        self.assertIn('"Kyber crystal" → "tinh thể Kyber" [LOCKED]', system)
        self.assertIn("Corrections for this episode", system)
        self.assertIn("Bob is being sarcastic", system)
        self.assertEqual(env.library.unit_summary(env.store.get_unit(unit))["status"], "current")

    def test_only_the_attached_corrections_are_used_by_a_retranslation(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        keep = env.service.add_correction(unit, {"lang": "vi", "kind": "note", "payload": {"text": "keep this tone"}})["ids"][0]
        env.service.add_correction(unit, {"lang": "vi", "kind": "note", "payload": {"text": "leave this out"}})
        env.service.retranslate(unit, {"lang": "vi", "correction_ids": [keep]})
        env.run_pending_retranslation()
        self.assertIn("keep this tone", env.fake.calls[-1].system)
        self.assertNotIn("leave this out", env.fake.calls[-1].system)
        with self.assertRaisesRegex(ValueError, "unknown or inactive"):
            env.service.retranslate(unit, {"lang": "vi", "correction_ids": [9999]})

    def test_new_corrections_can_be_added_in_the_retranslate_request(self):
        env = Env(self)
        env.translate(1)
        env.service.retranslate(env.unit(1), {"lang": "vi", "new_corrections": [
            {"kind": "note", "payload": {"text": "added in the form"}}]})
        env.run_pending_retranslation()
        self.assertIn("added in the form", env.fake.calls[-1].system)

    def test_removed_corrections_stop_applying(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        cid = env.service.add_correction(unit, {"lang": "vi", "kind": "note", "payload": {"text": "temporary"}})["ids"][0]
        self.assertTrue(env.service.remove_correction(unit, cid))
        env.service.retranslate(unit, {"lang": "vi"})
        env.run_pending_retranslation()
        self.assertNotIn("temporary", env.fake.calls[-1].system)

    def test_invalid_corrections_are_rejected_with_a_reason(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        cases = [
            ({"kind": "term", "payload": {"source": "x"}}, "'target' is required"),
            ({"kind": "address", "payload": {"a": "A", "b": "B"}}, "a_to_b"),
            ({"kind": "note", "scope": "series", "payload": {"text": "x"}}, "one episode"),
            ({"kind": "cue_edit", "scope": "forward", "payload": {"cue": 1, "text": "x"}}, "this episode only"),
            ({"kind": "cue_edit", "payload": {"cue": 99, "text": "x"}}, "between 1 and"),
            ({"kind": "cue_edit", "payload": {"cue": 1, "text": "  "}}, "cannot be empty"),
            ({"kind": "bogus", "payload": {}}, "kind must be"),
            ({"kind": "term", "scope": "galaxy", "payload": {}}, "scope must be"),
        ]
        for body, expected in cases:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, expected):
                env.service.add_correction(unit, {"lang": "vi", **body})

    def test_a_cue_edit_that_drops_a_tag_is_applied_with_a_warning(self):
        env = Env(self)
        env.translate(1)
        (env.media / "Show.S01E01.en.srt").write_text(source_text(1).replace("Line 2 of", "<i>Line 2</i> of"), encoding="utf-8")
        result = env.service.add_correction(env.unit(1), {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 2, "text": "plain"}})
        self.assertIn("tags differ", result["warnings"][0])

    def test_a_cue_edit_is_ignored_once_the_source_cue_changes(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 10, "text": "tay"}})
        (env.media / "Show.S01E01.en.srt").write_text(source_text(1).replace("Line 10 of", "A different line 10 of"), encoding="utf-8")
        env.service.retranslate(unit, {"lang": "vi"})
        env.run_pending_retranslation()
        self.assertIn(10, env.fake.calls[-1].meta["targets"])
        self.assertNotEqual(env.cue_texts(1)[9], "tay")


class RangeRetranslationTests(unittest.TestCase):
    def test_cue_range_requests_only_that_range_with_the_whole_source_as_context(self):
        env = Env(self)
        env.translate(1)
        before = env.cue_texts(1)
        env.prefix = "[v2] "
        queued = env.service.retranslate(env.unit(1), {"lang": "vi", "range": {"cues": [4, 6]}})
        self.assertEqual(queued["range"], {"first": 4, "last": 6})
        env.run_pending_retranslation()

        request = env.fake.calls[-1]
        self.assertEqual(request.meta["targets"], [4, 5, 6])
        self.assertEqual(sorted(request.meta["source"]), list(range(1, CUES + 1)))
        after = env.cue_texts(1)
        self.assertEqual(after[:3] + after[6:], before[:3] + before[6:])
        self.assertTrue(all(t.startswith("[v2] ") for t in after[3:6]))

    def test_time_range_picks_the_cues_it_overlaps(self):
        env = Env(self)
        env.translate(1)
        queued = env.service.retranslate(env.unit(1), {"lang": "vi", "range": {"time": ["00:00:04", "00:00:08"]}})
        self.assertEqual(queued["range"], {"first": 3, "last": 5})

    def test_bad_ranges_are_rejected_before_anything_is_queued(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        for spec, expected in [
            ({"cues": [6, 4]}, "range.cues"),
            ({"cues": [1, 99]}, "range.cues"),
            ({"time": ["01:00:00", "02:00:00"]}, "no cue falls"),
            ({"time": ["nope", "00:00:09"]}, "expected a time"),
            ({"time": ["00:00:09", "00:00:04"]}, "before the start"),
        ]:
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, expected):
                env.service.retranslate(unit, {"lang": "vi", "range": spec})
        self.assertEqual(list((Path(env.queue_dir) / "pending").glob("*.json")), [])

    def test_a_range_needs_an_existing_target_that_matches_the_source(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        Path(env.output(1)).write_text(srt.serialize(srt.parse_file(env.output(1))[:5]), encoding="utf-8")
        env.service.retranslate(unit, {"lang": "vi", "range": {"cues": [1, 2]}})
        with self.assertRaisesRegex(RuntimeError, "different number of cues"):
            env.run_pending_retranslation()
        Path(env.output(1)).unlink()
        with self.assertRaisesRegex(ValueError, "no target subtitle yet"):
            env.service.retranslate(unit, {"lang": "vi", "range": {"cues": [1, 2]}})


class QueueTests(unittest.TestCase):
    def test_a_retranslation_runs_ahead_of_older_automatic_jobs(self):
        env = Env(self)
        env.translate(1)
        auto = env.job(2)
        queue = gst_queue.JobQueue(env.queue_dir)
        auto_path = queue.queue_dir / "pending" / "auto-2.json"
        auto_path.write_text(json.dumps(auto), encoding="utf-8")
        import os
        os.utime(auto_path, (1, 1))  # much older than the retranslation

        env.service.retranslate(env.unit(1), {"lang": "vi"})
        seen = []
        queue.process_once(lambda job, update: seen.append(job["trigger"] if job.get("trigger") else "auto") or "ok")
        queue.process_once(lambda job, update: seen.append(job["trigger"] if job.get("trigger") else "auto") or "ok")
        self.assertEqual(seen, ["retranslate", "auto"])

    def test_retranslation_is_not_skipped_because_the_target_exists_but_automatic_jobs_are(self):
        env = Env(self)
        env.translate(1)
        job = env.job(1)
        self.assertTrue(gst_queue.should_skip_job(job))
        self.assertFalse(gst_queue.should_skip_job({**job, "trigger": "retranslate"}))

    def test_one_retranslation_per_target_at_a_time(self):
        env = Env(self)
        env.translate(1)
        env.service.retranslate(env.unit(1), {"lang": "vi"})
        with self.assertRaisesRegex(ValueError, "already queued"):
            env.service.retranslate(env.unit(1), {"lang": "vi"})
        env.run_pending_retranslation()
        env.service.retranslate(env.unit(1), {"lang": "vi"})  # fine once the first has left the queue


class HistoryTests(unittest.TestCase):
    def test_retranslate_then_revert_restores_the_earlier_hash_and_unlocks_undone_edits(self):
        """SPEC section 6, criterion 8, through the service."""
        env = Env(self)
        env.translate(1)
        before = sha(env.output(1))
        unit = env.unit(1)
        env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 10, "text": "tay"}})
        env.prefix = "[v2] "
        env.service.retranslate(unit, {"lang": "vi"})
        env.run_pending_retranslation()
        self.assertNotEqual(sha(env.output(1)), before)

        result = env.service.revert(unit, 1, "vi")

        self.assertEqual(sha(env.output(1)), before)
        self.assertEqual(result["restored"], 1)
        self.assertEqual(len(env.refreshed), 2)  # cue edit and revert; a retranslate job refreshes in process_job
        row = env.service.cues(unit, "vi")["rows"][9]
        self.assertFalse(row["locked"])  # the edit is gone from the file, so it no longer locks the cue

    def test_versions_list_diff_and_provenance(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 3, "text": "ba"}})
        listing = env.service.list_versions(unit, "vi")
        self.assertEqual([(v["version"], v["trigger"], v["current"]) for v in listing["versions"]],
                         [(2, "cue_edit", True), (1, "auto", False)])
        diff = env.service.diff(unit, 2, None, "vi")
        self.assertEqual((diff["version"], diff["against"], diff["changed_cues"]), (2, 1, 1))
        change = diff["changes"][0]
        self.assertEqual((change["before"][0]["n"], change["before"][0]["text"], change["after"][0]["text"]),
                         (3, "[vi] Line 3 of episode 1", "ba"))
        with self.assertRaisesRegex(ValueError, "no earlier version"):
            env.service.diff(unit, 1, None, "vi")
        with self.assertRaises(LookupError):
            env.service.diff(unit, 9, 1, "vi")

    def test_a_hand_edited_target_is_flagged_and_kept_as_an_external_version(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        Path(env.output(1)).write_text(Path(env.output(1)).read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertTrue(env.service.list_versions(unit, "vi")["modified_outside"])
        env.service.retranslate(unit, {"lang": "vi"})
        env.run_pending_retranslation()
        triggers = [v["trigger"] for v in env.service.list_versions(unit, "vi")["versions"]]
        self.assertEqual(triggers, ["retranslate", "external", "auto"])


class CuesViewTests(unittest.TestCase):
    def test_jump_to_a_player_time_search_and_lock_markers(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        env.service.add_correction(unit, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 4, "text": "bốn"}})

        everything = env.service.cues(unit, "vi")
        self.assertEqual((everything["total"], everything["aligned"], len(everything["rows"])), (CUES, True, CUES))
        self.assertEqual(everything["rows"][3]["target"], "bốn")
        self.assertTrue(everything["rows"][3]["locked"])
        self.assertEqual(everything["rows"][0]["source"], "Line 1 of episode 1")

        jumped = env.service.cues(unit, "vi", at="00:00:09", limit=4)
        self.assertEqual(jumped["focus"], 5)  # 8.0-9.5 s contains 00:00:09
        self.assertIn(5, [r["n"] for r in jumped["rows"]])
        between = env.service.cues(unit, "vi", at="0:00:07.9")  # in the gap: nearest start wins
        self.assertEqual(between["focus"], 5)

        found = env.service.cues(unit, "vi", q="kyber")
        self.assertEqual([r["n"] for r in found["rows"]], [5])
        self.assertEqual([r["n"] for r in env.service.cues(unit, "vi", q="bốn")["rows"]], [4])

    def test_player_times_are_parsed_leniently(self):
        self.assertEqual(parse_time_ms("00:23:10"), 1390000)
        self.assertEqual(parse_time_ms("23:10"), 1390000)
        self.assertEqual(parse_time_ms("1:02:03.5"), 3723500)
        with self.assertRaises(ValueError):
            parse_time_ms("soon")


class GlossaryPageTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        for episode in (1, 2):
            self.env.translate(episode)
        self.series = self.env.store.list_series()[0]["id"]

    def test_save_validates_backs_up_and_reports_how_many_translations_go_stale(self):
        first = "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n"
        preview = self.env.service.save_glossary(self.series, "vi", first, dry_run=True)
        self.assertEqual((preview["saved"], preview["would_stale"]), (False, 2))
        self.assertFalse((self.env.data / "series").exists())  # a dry run writes nothing

        saved = self.env.service.save_glossary(self.series, "vi", first)
        self.assertTrue(saved["saved"])
        self.assertEqual(self.env.service.glossary(self.series, "vi")["yaml"], first)
        self.assertEqual(self.env.service.glossary(self.series, "vi")["stale_units"], 2)

        self.env.service.save_glossary(self.series, "vi", first + "style: casual\n")
        backups = list((self.env.data / "backups" / "glossary" / "series" / "bazarr-7").glob("vi-*.yaml"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), first)

    def test_invalid_yaml_is_rejected_and_the_file_is_left_alone(self):
        good = "terms:\n  - {source: A, target: B}\n"
        self.env.service.save_glossary(self.series, "vi", good)
        for bad in ("terms: nope\n", "terms:\n  - {source: A}\n", "version: 9\n", "a: [unclosed\n"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.env.service.save_glossary(self.series, "vi", bad)
        self.assertEqual(self.env.service.glossary(self.series, "vi")["yaml"], good)

    def test_a_hand_broken_glossary_stops_the_job_instead_of_translating_without_it(self):
        path = self.env.data / "series" / "bazarr-7" / "glossary.vi.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("terms: [broken\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Glossary series/bazarr-7/vi is invalid"):
            self.env.translate(3)

    def test_unsafe_language_codes_are_rejected(self):
        with self.assertRaises(ValueError):
            self.env.service.glossary(self.series, "../../etc")


class ApiRoutingTests(unittest.TestCase):
    def call(self, env, method, path, query=None, body=None):
        return library_api.handle(env.service, method, path, query or {}, body)

    def test_routes_map_lookup_and_value_errors_to_404_and_400(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        series = env.store.list_series()[0]["id"]

        status, payload = self.call(env, "GET", "/api/library/series")
        self.assertEqual((status, payload["items"][0]["title"]), (200, "Show"))
        status, payload = self.call(env, "GET", f"/api/library/series/{series}/units", {"status": ["current"]})
        self.assertEqual((status, [u["episode"] for u in payload["units"]], payload["languages"]), (200, [1], ["vi"]))
        self.assertEqual(self.call(env, "GET", "/api/library/series/999/units")[0], 404)
        self.assertEqual(self.call(env, "GET", f"/api/library/series/{series}/units", {"status": ["odd"]})[0], 400)
        self.assertEqual(self.call(env, "GET", "/api/units/999/cues")[0], 404)
        self.assertEqual(self.call(env, "GET", f"/api/units/{unit}/cues", {"at": ["soon"]})[0], 400)
        status, payload = self.call(env, "GET", f"/api/units/{unit}/cues", {"lang": ["vi"], "q": ["kyber"]})
        self.assertEqual((status, payload["total"]), (200, 1))
        self.assertEqual(self.call(env, "GET", "/api/library/movies")[1], {"items": []})
        self.assertIsNone(self.call(env, "GET", "/api/queue"))
        self.assertIsNone(self.call(env, "DELETE", f"/api/units/{unit}"))

    def test_corrections_retranslate_versions_and_glossary_over_the_routes(self):
        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        series = env.store.list_series()[0]["id"]

        status, payload = self.call(env, "POST", f"/api/units/{unit}/corrections", body={
            "lang": "vi", "kind": "cue_edit", "payload": {"cue": 2, "text": "hai"}})
        self.assertEqual((status, payload["version"]), (200, 2))
        status, payload = self.call(env, "GET", f"/api/units/{unit}/corrections", {"lang": ["vi"]})
        self.assertEqual([c["kind"] for c in payload["items"]], ["cue_edit"])
        status, payload = self.call(env, "POST", f"/api/units/{unit}/retranslate", body={"lang": "vi", "range": {"cues": [1, 2]}})
        self.assertEqual((status, payload["range"]), (200, {"first": 1, "last": 2}))
        self.assertEqual(self.call(env, "POST", f"/api/units/{unit}/retranslate", body={"lang": "vi"})[0], 400)  # queued
        status, payload = self.call(env, "GET", f"/api/units/{unit}/versions", {"lang": ["vi"]})
        self.assertEqual(len(payload["versions"]), 2)
        status, payload = self.call(env, "GET", f"/api/units/{unit}/versions/2/diff", {"against": ["1"], "lang": ["vi"]})
        self.assertEqual((status, payload["changed_cues"]), (200, 1))
        status, payload = self.call(env, "POST", f"/api/units/{unit}/versions/1/revert", body={"lang": "vi"})
        self.assertEqual((status, payload["restored"]), (200, 1))
        self.assertEqual(self.call(env, "POST", f"/api/units/{unit}/versions/9/revert", body={"lang": "vi"})[0], 404)

        status, payload = self.call(env, "PUT", f"/api/series/{series}/glossary", {"lang": ["vi"]}, {"yaml": "style: x\n"})
        self.assertEqual((status, payload["saved"]), (200, True))
        status, payload = self.call(env, "GET", f"/api/series/{series}/glossary", {"lang": ["vi"]})
        self.assertEqual((status, payload["yaml"]), (200, "style: x\n"))
        self.assertEqual(self.call(env, "GET", f"/api/series/{series}/glossary")[0], 400)  # lang is required


class RulesTests(unittest.TestCase):
    def test_an_address_correction_replaces_the_files_rule_for_that_pair_from_its_cue_on(self):
        from subcore import glossary as g
        from gst_worker.rules import build_rules

        glossary = g.loads("address_rules:\n  - {a: Alice, b: Bob, a_to_b: 'tôi–anh', from: S01E01}\n"
                           "  - {a: Carol, b: Dan, a_to_b: 'chị–em'}\n")
        correction = {"kind": "address", "payload": {"a": "Bob", "b": "Alice", "a_to_b": "anh–em", "from_cue": 6}}

        rules = build_rules(glossary, season=1, episode=2, corrections=[correction])

        shown = [(r.rule.a, r.rule.a_to_b, r.from_cue, r.until_cue) for r in rules.active_rules]
        self.assertEqual(shown, [("Alice", "tôi–anh", None, 6), ("Carol", "chị–em", None, None), ("Bob", "anh–em", 6, None)])
        whole_episode = build_rules(
            glossary, season=1, episode=2,
            corrections=[{"kind": "address", "payload": {"a": "Bob", "b": "Alice", "a_to_b": "anh–em"}}],
        )
        self.assertEqual([(r.rule.a, r.from_cue) for r in whole_episode.active_rules], [("Carol", None), ("Bob", None)])

    def test_the_hash_tracks_rules_but_not_hand_edited_cues(self):
        from subcore import glossary as g
        from gst_worker.rules import build_rules

        plain = build_rules(g.Glossary(), season=1, episode=1)
        with_edit = build_rules(g.Glossary(), season=1, episode=1,
                                corrections=[{"kind": "cue_edit", "payload": {"cue": 1, "text": "x"}}])
        with_note = build_rules(g.Glossary(), season=1, episode=1,
                                corrections=[{"kind": "note", "payload": {"text": "x"}}])
        self.assertEqual(plain.hash, with_edit.hash)
        self.assertNotEqual(plain.hash, with_note.hash)
        self.assertEqual(with_edit.locked, {1: "x"})


class HttpRoutingTests(unittest.TestCase):
    """The real handler: library routes sit behind Basic Auth like everything else."""

    def test_library_routes_are_served_over_http_and_need_credentials(self):
        import base64
        import http.client
        import threading
        from http.server import ThreadingHTTPServer

        import worker
        from gst_worker.auth import ConsoleAuth

        env = Env(self)
        env.translate(1)
        unit = env.unit(1)
        series = env.store.list_series()[0]["id"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.ConsoleHandler)
        server.ctx = {"units": env.service, "static_dir": str(env.root), "queue_dir": env.queue_dir}
        server.auth = ConsoleAuth.from_env({"CONSOLE_USER": "u", "CONSOLE_PASSWORD": "p"})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

        def request(method, path, body=None, headers=auth):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            try:
                payload = None if body is None else json.dumps(body)
                conn.request(method, path, body=payload, headers=dict(headers))
                response = conn.getresponse()
                return response.status, json.loads(response.read().decode("utf-8"))
            finally:
                conn.close()

        self.assertEqual(request("GET", "/api/library/series", headers={})[0], 401)
        self.assertEqual(request("PUT", f"/api/series/{series}/glossary?lang=vi", {"yaml": ""}, headers={})[0], 401)
        status, body = request("GET", "/api/library/series")
        self.assertEqual((status, body["items"][0]["units"]), (200, 1))
        status, body = request("GET", f"/api/units/{unit}/cues?lang=vi&at=00:00:05")
        self.assertEqual((status, body["focus"]), (200, 3))
        status, body = request("PUT", f"/api/series/{series}/glossary?lang=vi", {"yaml": "style: x\n"})
        self.assertEqual((status, body["saved"]), (200, True))
        status, body = request("PUT", f"/api/series/{series}/glossary?lang=vi", {"yaml": "terms: nope\n"})
        self.assertEqual(status, 400)
        self.assertIn("terms: expected a list", body["error"])
        status, body = request("POST", f"/api/units/{unit}/retranslate", {"lang": "vi"})
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(request("GET", "/api/units/999")[0], 404)


class WorkerWiringTests(unittest.TestCase):
    def test_process_job_enriches_the_identity_from_bazarr_when_it_can(self):
        from gst_worker.library import enrich_job_identity

        class Bazarr:
            def get_json(self, url, params=None, headers=None):
                if url.endswith("/api/series"):
                    return {"data": [{"title": "Malcolm in the Middle", "tvdbId": 70327}]}
                return {"data": [{"season": 1, "episode": 1, "title": "Pilot"}]}

        job = {"media_type": "series", "series_id": "7", "media_id": "9", "video_path": "/m/odd name.mkv"}
        enrich_job_identity(job, Bazarr(), "http://bazarr", "key")
        ident = identify_job(job)
        self.assertEqual((ident.series_key, ident.series_title, ident.season, ident.episode, ident.title),
                         ("tvdb-70327", "Malcolm in the Middle", 1, 1, "Pilot"))

    def test_a_bazarr_failure_never_fails_the_job(self):
        from gst_worker.library import enrich_job_identity

        class Down:
            def get_json(self, *args, **kwargs):
                raise OSError("connection refused")

        job = {"media_type": "series", "series_id": "7", "media_id": "9", "video_path": "/m/Show.S01E02.mkv"}
        enrich_job_identity(job, Down(), "http://bazarr", "key")
        self.assertEqual(identify_job(job).episode, 2)


if __name__ == "__main__":
    unittest.main()
