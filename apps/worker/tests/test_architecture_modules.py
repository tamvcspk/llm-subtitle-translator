import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from gst_worker.console import ConsoleActions
from gst_worker.queue_policy import daily_quota_retry_at, provider_retry_decision


class QueuePolicyTests(unittest.TestCase):
    def test_provider_retry_policy_is_pure_and_bounded(self):
        self.assertEqual(provider_retry_decision(0, 1000).retry_at, 1300)
        self.assertEqual(provider_retry_decision(3, 1000).retry_count, 4)
        self.assertEqual(provider_retry_decision(4, 1000).state, "failed")
        self.assertEqual(provider_retry_decision(4, 1000, fallback_available=True).state, "fallback")

    def test_daily_quota_pause_is_24_hours(self):
        self.assertEqual(daily_quota_retry_at(1000), 87400)


class ConsoleActionsTests(unittest.TestCase):
    def test_actions_delegate_to_the_job_queue_port(self):
        jobs = Mock()
        jobs.retry.return_value = True
        actions = ConsoleActions(jobs)
        self.assertTrue(actions.retry("job"))
        jobs.retry.assert_called_once_with("job")
        actions.cancel("job")
        jobs.cancel.assert_called_once_with("job")
        actions.delete("failed", "job")
        jobs.delete.assert_called_once_with("failed", "job")

    def test_enqueue_delegates_job_and_targets(self):
        jobs = Mock()
        jobs.enqueue.return_value = ["a"]
        self.assertEqual(ConsoleActions(jobs).enqueue({"job_id": "a"}, [{"code": "zh"}]), ["a"])
        jobs.enqueue.assert_called_once_with({"job_id": "a"}, [{"code": "zh"}])

    def test_a_picked_subtitle_becomes_one_job_per_configured_target(self):
        jobs = Mock()
        jobs.enqueue.return_value = ["a", "b"]
        targets = [{"code": "vi"}, {"code": "de"}]
        got = ConsoleActions(jobs).enqueue_subtitle({"video_path": "/m/v.mkv", "subtitle_path": "/m/v.en.srt"}, targets)
        self.assertEqual(got, ["a", "b"])
        base, passed = jobs.enqueue.call_args.args
        self.assertEqual(passed, targets)
        self.assertEqual(base, {
            "video_path": "/m/v.mkv", "subtitle_path": "/m/v.en.srt", "provider": "gemini-console",
            "source_code": "en", "source_language": "English", "series_id": "", "media_id": "", "media_type": "movie",
        })

    def test_the_request_may_name_the_target_codes_and_old_field_names(self):
        jobs = Mock()
        ConsoleActions(jobs).enqueue_subtitle(
            {"target_codes": ["de"], "language": "fr", "type": "series", "provider": "x"}, [{"code": "vi"}, {"code": "de"}]
        )
        base, passed = jobs.enqueue.call_args.args
        self.assertEqual(passed, [{"code": "de"}])
        self.assertEqual((base["source_code"], base["media_type"], base["provider"]), ("fr", "series", "x"))

    def test_a_scan_queues_each_item_with_the_targets_it_is_missing(self):
        jobs = Mock()
        jobs.enqueue.side_effect = [["a"], ["b", "c"]]
        found = [
            {"video_path": "/m/1.mkv", "subtitle_path": "/m/1.en.srt", "source_code": "en", "source_language": "English",
             "missing_targets": [{"code": "vi"}]},
            {"video_path": "/m/2.mkv", "subtitle_path": "/m/2.fr.srt", "source_code": "fr", "source_language": "French",
             "missing_targets": [{"code": "vi"}, {"code": "de"}]},
        ]
        self.assertEqual(ConsoleActions(jobs).enqueue_found(found), ["a", "b", "c"])
        second = jobs.enqueue.call_args_list[1].args
        self.assertEqual(second[0]["source_code"], "fr")
        self.assertEqual(second[0]["media_type"], "movie")
        self.assertEqual(second[1], [{"code": "vi"}, {"code": "de"}])


if __name__ == "__main__":
    unittest.main()
