import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gst_worker.console import ConsoleActions
from gst_worker.queue_policy import daily_quota_retry_at, provider_retry_decision
from gst_worker.translation_attempt import TranslationAttempt


class QueuePolicyTests(unittest.TestCase):
    def test_provider_retry_policy_is_pure_and_bounded(self):
        self.assertEqual(provider_retry_decision(0, 1000).retry_at, 1300)
        self.assertEqual(provider_retry_decision(3, 1000).retry_count, 4)
        self.assertEqual(provider_retry_decision(4, 1000).state, "failed")
        self.assertEqual(provider_retry_decision(4, 1000, fallback_available=True).state, "fallback")

    def test_daily_quota_pause_is_24_hours(self):
        self.assertEqual(daily_quota_retry_at(1000), 87400)


class ConsoleActionsTests(unittest.TestCase):
    def test_actions_delegate_with_bound_queue_directory(self):
        actions = ConsoleActions("queue")
        with patch("gst_worker.console.retry_failed_job", return_value=True) as retry:
            self.assertTrue(actions.retry("job"))
        retry.assert_called_once_with("queue", "job")

    def test_enqueue_delegates_job_and_targets(self):
        actions = ConsoleActions("queue")
        with patch("gst_worker.console.enqueue_translation_jobs", return_value=["a"]) as enqueue:
            self.assertEqual(actions.enqueue({"job_id": "a"}, [{"code": "zh"}]), ["a"])
        enqueue.assert_called_once_with("queue", {"job_id": "a"}, [{"code": "zh"}])


class TranslationAttemptTests(unittest.TestCase):
    def test_attempt_is_the_single_observation_and_execution_seam(self):
        attempt = TranslationAttempt()
        with patch("gst_worker.translation_attempt.translation.translation_progress", return_value={"progress_checkpoint": 4}) as progress:
            self.assertEqual(attempt.progress({"subtitle_path": "x"}), {"progress_checkpoint": 4})
        progress.assert_called_once()


if __name__ == "__main__":
    unittest.main()
