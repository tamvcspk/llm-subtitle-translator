"""Contract of the LLM call log on the real SQLite store (ADR-12)."""

import tempfile
import time
import unittest
from pathlib import Path

from subserver.domain.ports import LlmCall, TokenTotals
from subserver.adapters.store import Store


def record(log: Store, job_id="j1", *, tokens_in=10, tokens_out=5, at=None, task="translate", outcome="ok"):
    log.record_llm_call(
        job_id, provider="gemini", model="m-1", task=task, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=120, outcome=outcome, at=at,
    )


class UsageLogContract:
    """Mixed into a TestCase that provides ``self.log``."""

    log: Store

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

    def test_recording_returns_the_id_and_keeps_the_details_of_the_call(self):
        first = self.log.record_llm_call(
            "j1", provider="gemini", model="m-1", task="translate", tokens_in=1, tokens_out=2, latency_ms=3,
            outcome="ok", at=1.0, kind="repair", prompt_version="p3-1", bundle_hash="b", style_hash="s",
            glossary_hash="g", batch=2, http_status=200, finish_reason="STOP", retries=1, repairs=3, cost_est=0.25,
        )
        second = self.log.record_llm_call(
            "j1", provider="gemini", model="m-1", task="translate", tokens_in=1, tokens_out=2, latency_ms=3,
            outcome="unavailable", at=2.0,
        )
        self.assertNotEqual(first, second)
        full, plain = self.log.llm_calls("j1")
        self.assertEqual(full.id, first)
        self.assertEqual(
            (full.kind, full.prompt_version, full.bundle_hash, full.style_hash, full.glossary_hash, full.batch,
             full.http_status, full.finish_reason, full.retries, full.repairs, full.cost_est),
            ("repair", "p3-1", "b", "s", "g", 2, 200, "STOP", 1, 3, 0.25),
        )
        self.assertEqual((plain.kind, plain.http_status, plain.retries, plain.repairs, plain.cost_est), ("", None, 0, 0, 0.0))

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


if __name__ == "__main__":
    unittest.main()
