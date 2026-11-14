"""Translate only what is missing, never the whole library, and only escalate the jobs that need it."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import worker
from gst_worker import bazarr, queue, subtitles, translation

TARGETS = [{"code": "vi", "language": "Vietnamese", "enabled": True}]
LITE = "gemini-3.1-flash-lite"
STRONG = "gemini-3.5-flash"
MODELS = (LITE, STRONG)


class Workdir(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue_dir = str(self.root / "queue")
        self.media = self.root / "media"
        self.media.mkdir()

    def source(self, stem="Show.S01E01", folder=None):
        directory = folder or self.media
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}.en.srt"
        path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
        os.utime(path, (100, 100))  # long settled, and never ahead of the clock
        return path

    def enqueue(self, path):
        return queue.enqueue_translation_jobs(self.queue_dir, {"subtitle_path": str(path), "source_code": "en"}, TARGETS)

    def env(self, **values):
        patcher = patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)


class ExistingTargetTests(Workdir):
    def test_any_sibling_subtitle_in_the_language_counts_as_present(self):
        source = self.source()
        self.assertIsNone(subtitles.existing_target_subtitle(str(source), "vi", "en"))
        for name in ("Show.S01E01.vi.hi.srt", "Show.S01E01.vi.ass", "Show.S01E01.1080p.vi.srt"):
            found = self.media / name
            found.write_text("x", encoding="utf-8")
            self.assertEqual(subtitles.existing_target_subtitle(str(source), "vi", "en"), str(found))
            found.unlink()

    def test_forced_other_language_and_other_episode_do_not_count(self):
        source = self.source()
        for name in ("Show.S01E01.vi.forced.srt", "Show.S01E01.fr.srt", "Show.S01E02.vi.srt", "Show.S01E01.txt"):
            (self.media / name).write_text("x", encoding="utf-8")
        self.assertIsNone(subtitles.existing_target_subtitle(str(source), "vi", "en"))

    def test_enqueue_and_scan_skip_a_language_that_is_already_there(self):
        source = self.source()
        (self.media / "Show.S01E01.vi.hi.srt").write_text("x", encoding="utf-8")
        self.assertEqual(self.enqueue(source), [])
        found = subtitles.scan_source_subtitles(
            [str(self.media)], [{"code": "en", "language": "English", "enabled": True}], TARGETS
        )
        self.assertEqual(found, [])

    def test_skip_reason_names_the_present_subtitle_but_never_blocks_a_retranslation(self):
        source = self.source()
        (self.media / "Show.S01E01.vi.ass").write_text("x", encoding="utf-8")
        job = {"subtitle_path": str(source), "source_code": "en", "target_code": "vi"}
        self.assertIn("Show.S01E01.vi.ass", queue.skip_reason(job))
        self.assertIsNone(queue.skip_reason({**job, "trigger": "retranslate"}))


class AdmissionGuardTests(Workdir):
    def test_only_paths_keeps_the_worker_inside_the_named_folder(self):
        inside = self.source("Test.S01E01", self.media / "Test Show")
        outside = self.source("Other.S01E01", self.media / "Other Show")
        self.env(GST_ONLY_PATHS=str(self.media / "Test Show"))
        self.assertEqual(len(self.enqueue(inside)), 1)
        self.assertEqual(self.enqueue(outside), [])
        # A job that reached the queue another way is still not translated.
        job = {"subtitle_path": str(outside), "source_code": "en", "target_code": "vi"}
        self.assertEqual(queue.skip_reason(job), "outside GST_ONLY_PATHS")

    def test_only_paths_matches_whole_folders_not_name_prefixes(self):
        self.env(GST_ONLY_PATHS="/media/Show")
        self.assertFalse(queue.outside_allowed_paths("/media/Show/S01/a.en.srt"))
        self.assertTrue(queue.outside_allowed_paths("/media/Show 2/a.en.srt"))

    def test_max_pending_stops_a_flood_of_wanted_items(self):
        self.env(GST_MAX_PENDING="2")
        created = [self.enqueue(self.source(f"Show.S01E0{n}")) for n in range(1, 6)]
        self.assertEqual(sum(len(batch) for batch in created), 2)
        self.assertEqual(queue.open_job_count(self.queue_dir), 2)

    def test_the_bazarr_script_obeys_the_same_guards_and_variants(self):
        script = (Path(__file__).resolve().parents[3] / "bazarr-postprocess" / "gst_enqueue.sh").read_text(encoding="utf-8")
        payload = script.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        queue.ensure_queue_dirs(self.queue_dir)  # the shell wrapper creates these before the payload runs
        (self.root / "targets.json").write_text(json.dumps({
            "source_languages": [{"code": "en", "language": "English"}], "target_languages": TARGETS,
        }), encoding="utf-8")

        def run(source, **extra):
            environment = {
                **os.environ, "QUEUE_DIR": self.queue_dir, "TARGETS_FILE": str(self.root / "targets.json"),
                "SUBTITLE_PATH": str(source), "VIDEO_PATH": str(source.with_suffix(".mkv")), "LANGUAGE": "en",
                "PROVIDER": "embeddedsubtitles", "SERIES_ID": "", "MEDIA_ID": "", "MEDIA_TYPE": "movie",
                "GST_ONLY_PATHS": "", "GST_MAX_PENDING": "0", **extra,
            }
            return subprocess.run([sys.executable, "-"], input=payload, env=environment, text=True,
                                  capture_output=True, check=True).stdout

        present = self.source("Has.Target")
        (self.media / "Has.Target.vi.hi.srt").write_text("x", encoding="utf-8")
        self.assertIn("no target jobs", run(present))

        outside = self.source("Outside", self.media / "Other")
        self.assertIn("outside GST_ONLY_PATHS", run(outside, GST_ONLY_PATHS=str(self.media / "Test")))

        for n in range(1, 4):
            run(self.source(f"Many.S01E0{n}"), GST_MAX_PENDING="2")
        self.assertEqual(queue.open_job_count(self.queue_dir), 2)


class FakeMedia:
    """MediaLibrary port double: records what the job asked and what was rescanned."""

    def __init__(self, missing):
        self.missing = missing
        self.asked = []
        self.refreshed = []

    def target_still_missing(self, job):
        self.asked.append(job)
        return self.missing

    def refresh(self, job):
        self.refreshed.append(job)


class NoMetadata:
    def description(self, job):
        return ""


class BazarrAuthorityTests(unittest.TestCase):
    def ask(self, item, job=None):
        http = Mock()
        http.get_json.return_value = {"data": [item]} if item is not None else {"data": []}
        job = job or {"media_type": "series", "media_id": "7", "target_code": "vi"}
        return bazarr.target_still_missing(job, http, "http://bazarr", "key"), http

    def test_bazarr_listing_the_language_as_missing_means_translate(self):
        result, http = self.ask({"missing_subtitles": [{"code2": "vi"}]})
        self.assertIs(result, True)
        self.assertEqual(http.get_json.call_args.args[0], "http://bazarr/api/episodes")

    def test_bazarr_not_listing_the_language_means_do_not_translate(self):
        self.assertIs(self.ask({"missing_subtitles": [{"code2": "fr"}]})[0], False)
        self.assertIs(self.ask({"missing_subtitles": []})[0], False)

    def test_movies_use_the_radarr_endpoint(self):
        result, http = self.ask({"missing_subtitles": [{"code2": "vi"}]}, {"media_type": "movie", "media_id": "3", "target_code": "vi"})
        self.assertIs(result, True)
        self.assertEqual(http.get_json.call_args.args[0], "http://bazarr/api/movies")

    def test_unknown_answers_fall_back_to_the_files(self):
        self.assertIsNone(self.ask(None)[0])
        self.assertIsNone(self.ask({"title": "no missing list"})[0])
        self.assertIsNone(self.ask({"missing_subtitles": []}, {"media_type": "movie", "target_code": "vi"})[0])
        broken = Mock()
        broken.get_json.side_effect = OSError("bazarr down")
        job = {"media_type": "series", "media_id": "7", "target_code": "vi"}
        self.assertIsNone(bazarr.target_still_missing(job, broken, "http://bazarr", "key"))
        self.assertIsNone(bazarr.target_still_missing(job, broken, "http://bazarr", ""))

    def test_process_job_spends_no_model_call_when_bazarr_does_not_want_the_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Show.S01E01.en.srt"
            subtitle.write_text("subtitle", encoding="utf-8")
            job = {"subtitle_path": str(subtitle), "source_code": "en", "target_code": "vi",
                   "media_type": "series", "media_id": "7", "unit_id": 1}
            settings = {"bazarr_url": "http://bazarr", "bazarr_api_key": "k", "tmdb_api_key": ""}
            attempt = Mock()
            media = FakeMedia(missing=False)
            self.assertEqual(
                worker.process_job(job, settings, None, None, None, attempt, media=media, metadata=NoMetadata()),
                "skipped-not-wanted",
            )
            attempt.run.assert_not_called()
            self.assertEqual(media.refreshed, [])

            attempt.run.return_value = "translated"
            media = FakeMedia(missing=None)
            self.assertEqual(
                worker.process_job(job, settings, None, None, None, attempt, media=media, metadata=NoMetadata()),
                "translated",
            )

            retranslate = {**job, "trigger": "retranslate"}
            media = FakeMedia(missing=False)
            self.assertEqual(
                worker.process_job(retranslate, settings, None, None, None, attempt, media=media, metadata=NoMetadata()),
                "translated",
            )
            self.assertEqual(media.asked, [])  # a retranslation is not asked about; Bazarr is only told to rescan
            self.assertEqual(len(media.refreshed), 1)


class RescanFailureTests(unittest.TestCase):
    def test_a_bazarr_that_cannot_rescan_does_not_fail_a_finished_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            subtitle = Path(tmp) / "Show.S01E01.en.srt"
            subtitle.write_text("subtitle", encoding="utf-8")
            job = {"subtitle_path": str(subtitle), "source_code": "en", "target_code": "vi", "unit_id": 1}
            attempt = Mock()
            attempt.run.return_value = "translated"
            media = FakeMedia(missing=None)
            media.refresh = Mock(side_effect=TimeoutError("timed out"))
            with self.assertLogs(level="WARNING") as logs:
                status = worker.process_job(job, {}, None, None, None, attempt, media=media, metadata=NoMetadata())
            self.assertEqual(status, "translated")
            self.assertIn("could not be asked to rescan", logs.output[0])


class EscalationTests(Workdir):
    def setUp(self):
        super().setUp()
        self.jobs = queue.JobQueue(self.queue_dir)

    def process(self, execute, models=MODELS):
        return self.jobs.process_once(execute, models=models)

    def only_job(self, state):
        [path] = list((Path(self.queue_dir) / state).glob("*.json"))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_hard_job_moves_to_the_strong_model_once_and_finishes_there(self):
        self.enqueue(self.source())
        used = []

        def execute(job, update_status):
            used.append(job["gst_model"])
            if job["gst_model"] == LITE:
                raise translation.HardJobError("Translation failed: TranslationError: 3 cues stayed invalid")
            return "translated"

        self.assertTrue(self.process(execute))
        pending = self.only_job("pending")
        self.assertTrue(pending["use_fallback_model"])
        self.assertEqual(pending["escalated_from"], LITE)
        self.assertTrue(self.process(execute))
        self.assertEqual(used, [LITE, STRONG])
        done = self.only_job("done")
        self.assertEqual((done["gst_model"], done["escalated_from"], done["result"]), (STRONG, LITE, "translated"))

    def test_a_job_the_strong_model_cannot_finish_fails_instead_of_looping(self):
        self.enqueue(self.source())
        execute = Mock(side_effect=translation.HardJobError("still invalid"))
        self.process(execute)
        self.process(execute)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(self.only_job("failed")["escalated_from"], LITE)

    def test_without_a_strong_model_a_hard_job_fails_at_once(self):
        self.enqueue(self.source())
        execute = Mock(side_effect=translation.HardJobError("invalid"))
        self.process(execute, models=(LITE,))
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(len(list((Path(self.queue_dir) / "failed").glob("*.json"))), 1)

    def test_an_ordinary_error_does_not_buy_a_strong_model_call(self):
        self.enqueue(self.source())
        execute = Mock(side_effect=RuntimeError("The provider rejected the API key"))
        self.process(execute)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(len(list((Path(self.queue_dir) / "failed").glob("*.json"))), 1)

    def test_a_manual_retry_starts_again_on_the_default_model(self):
        self.enqueue(self.source())
        execute = Mock(side_effect=translation.HardJobError("invalid"))
        self.process(execute)
        self.process(execute)
        failed = self.only_job("failed")
        self.assertTrue(queue.retry_failed_job(self.queue_dir, failed["job_id"]))
        pending = self.only_job("pending")
        self.assertNotIn("escalated_from", pending)
        self.assertNotIn("use_fallback_model", pending)

    def test_layer_report_shows_whether_the_strong_model_earns_its_place(self):
        for n in range(1, 4):
            self.enqueue(self.source(f"Show.S01E0{n}"))

        def execute(job, update_status):
            # Episode 1 translates on lite, episode 2 needs the strong model, episode 3 fails on both.
            if "E03" in job["subtitle_path"] or ("E02" in job["subtitle_path"] and job["gst_model"] == LITE):
                raise translation.HardJobError("invalid")
            return "translated"

        for _ in range(6):
            self.process(execute)
        report = queue.layer_report(self.queue_dir)
        self.assertEqual(report["escalated"], {"total": 2, "done": 1, "failed": 1, "waiting": 0})
        self.assertEqual(report["by_model"][LITE]["done"], 1)
        self.assertEqual(report["by_model"][STRONG], {"done": 1, "failed": 1})


if __name__ == "__main__":
    unittest.main()
