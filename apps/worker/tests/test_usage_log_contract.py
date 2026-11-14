"""Contract of the UsageLog port (SPEC criterion 10b): the same checks on SQLite and on an in-memory fake."""

import tempfile
import time
import unittest
from pathlib import Path

from gst_worker.ports import LlmCall, TokenTotals, UsageLog
from gst_worker.store import Store


class InMemoryUsageLog:
    """Fake UsageLog for tests that do not need a database."""

    def __init__(self) -> None:
        self._calls: list[LlmCall] = []

    def record_llm_call(self, job_id, *, provider, model, task, tokens_in, tokens_out, latency_ms, outcome, at=None):
        self._calls.append(
            LlmCall(
                id=len(self._calls) + 1, job_id=job_id, provider=provider, model=model, task=task,
                tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms, outcome=outcome,
                at=at or time.time(),
            )
        )

    def llm_calls(self, job_id=None):
        return [c for c in self._calls if job_id is None or c.job_id == job_id]

    def token_totals(self, since=0.0):
        calls = [c for c in self._calls if c.at >= since]
        return TokenTotals(
            calls=len(calls), tokens_in=sum(c.tokens_in for c in calls), tokens_out=sum(c.tokens_out for c in calls)
        )


def record(log: UsageLog, job_id="j1", *, tokens_in=10, tokens_out=5, at=None, task="translate", outcome="ok"):
    log.record_llm_call(
        job_id, provider="gemini", model="m-1", task=task, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=120, outcome=outcome, at=at,
    )


class UsageLogContract:
    """Mixed into a TestCase that provides ``self.log``."""

    log: UsageLog

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.log, UsageLog)

    def test_empty_log(self):
        self.assertEqual(self.log.llm_calls(), [])
        self.assertEqual(self.log.token_totals(), TokenTotals(0, 0, 0))

    def test_calls_come_back_as_named_immutable_values_in_recorded_order(self):
        record(self.log, "j1", task="translate", at=100.0)
        record(self.log, "j1", task="extract", outcome="rate_limited", at=200.0)
        first, second = self.log.llm_calls()
        self.assertIsInstance(first, LlmCall)
        self.assertEqual(
            (first.job_id, first.provider, first.model, first.task, first.tokens_in, first.tokens_out,
             first.latency_ms, first.outcome, first.at),
            ("j1", "gemini", "m-1", "translate", 10, 5, 120, "ok", 100.0),
        )
        self.assertEqual((second.task, second.outcome), ("extract", "rate_limited"))
        with self.assertRaises(AttributeError):
            first.model = "other"

    def test_filters_by_job(self):
        record(self.log, "a")
        record(self.log, "b")
        record(self.log, "a")
        self.assertEqual(len(self.log.llm_calls("a")), 2)
        self.assertEqual(len(self.log.llm_calls("b")), 1)
        self.assertEqual(self.log.llm_calls("missing"), [])
        self.assertEqual(len(self.log.llm_calls()), 3)

    def test_at_defaults_to_now(self):
        before = time.time()
        record(self.log)
        self.assertGreaterEqual(self.log.llm_calls()[0].at, before)

    def test_totals_sum_tokens_and_respect_since(self):
        record(self.log, tokens_in=3, tokens_out=4, at=100.0)
        record(self.log, tokens_in=5, tokens_out=6, at=200.0)
        self.assertEqual(self.log.token_totals(), TokenTotals(calls=2, tokens_in=8, tokens_out=10))
        self.assertEqual(self.log.token_totals(since=150.0), TokenTotals(calls=1, tokens_in=5, tokens_out=6))
        self.assertEqual(self.log.token_totals(since=200.0).calls, 1)  # inclusive
        self.assertEqual(self.log.token_totals(since=201.0), TokenTotals(0, 0, 0))


class SqliteUsageLogTests(UsageLogContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = Store(Path(directory.name) / "app.db")
        self.addCleanup(self.log.close)


class InMemoryUsageLogTests(UsageLogContract, unittest.TestCase):
    def setUp(self):
        self.log = InMemoryUsageLog()


if __name__ == "__main__":
    unittest.main()
