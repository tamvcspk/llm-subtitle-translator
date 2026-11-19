import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from subserver.console import ConsoleActions
from subserver.queue_policy import PROVIDER_RETRY_DELAYS, daily_quota_reset_at, next_utc_midnight


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

    def test_the_console_has_no_door_that_makes_jobs_without_the_request_planner(self):
        """R10-8: jobs come from ``JobRequests`` (Ignored, estimate, ``GST_ONLY_PATHS``, unit id) or the policy, never
        straight from a request body."""
        self.assertFalse(hasattr(ConsoleActions(Mock()), "enqueue"))
        self.assertFalse(hasattr(ConsoleActions(Mock()), "enqueue_subtitle"))
        web = (Path(__file__).resolve().parents[1] / "subserver" / "web.py").read_text(encoding="utf-8")
        self.assertNotIn('"/api/enqueue"', web)


if __name__ == "__main__":
    unittest.main()
