import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from gst_worker.console import ConsoleActions
from gst_worker.queue_policy import PROVIDER_RETRY_DELAYS, daily_quota_reset_at, next_utc_midnight


class QueuePolicyTests(unittest.TestCase):
    def test_the_breaker_rests_for_the_steps_the_retry_policy_always_had(self):
        self.assertEqual(PROVIDER_RETRY_DELAYS, (300, 900, 1800, 3600))

    def test_the_daily_quota_resets_at_the_next_pacific_midnight_never_more_than_a_day_away(self):
        for now in (1_800_000_000.0, 1_810_000_000.0, 1_820_000_000.0):
            self.assertTrue(0 < daily_quota_reset_at(now) - now <= 86_400 + 3_600)

    def test_a_day_cap_counts_from_zero_at_utc_midnight(self):
        self.assertEqual(next_utc_midnight(1_800_000_000.0), 1_800_057_600.0)


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
