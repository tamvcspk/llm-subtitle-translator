"""What a model call leaves behind for the worker's records (P5, FR-19.1, 19.4): the call log and the validator's findings."""

import unittest

from subcore.providers import FakeProvider, RateLimited, ResilientProvider, Unavailable
from subcore.providers.base import Capabilities, LLMRequest, LLMResponse
from subcore.providers.fake import echo_items
from subcore.providers.gemini import map_api_error
from subcore.translate import TranslationError, translate

from .helpers import numbered


def drop(ids, on_calls=None):
    def handler(req, call_no):
        items = echo_items(req, "[vi] ")
        if on_calls is None or call_no in on_calls:
            items = [it for it in items if it["i"] not in ids]
        return items

    return handler


def resilient(inner, logs):
    return ResilientProvider(inner, on_call=logs.append, base_delay=0.0, max_attempts=3, sleep=lambda _s: None)


class CallLogTests(unittest.TestCase):
    def test_a_good_call_records_how_it_was_made_and_what_was_said(self):
        logs = []
        provider = resilient(FakeProvider(drop({3, 5, 7}, on_calls={1})), logs)
        translate(provider, numbered(10))

        first, repair = logs
        self.assertEqual((first.kind, first.targets, first.retry, first.http_status, first.batch), ("translate", 10, 0, 200, 0))
        self.assertEqual((repair.kind, repair.targets), ("repair", 3))
        self.assertIsInstance(first.request, LLMRequest)
        self.assertIn("line 1", first.request.user)
        self.assertIn("[vi] line 1", first.response_text)

    def test_every_part_of_a_long_episode_knows_its_number(self):
        logs = []
        small = FakeProvider(caps=Capabilities(max_output_tokens=2000))  # too small for 400 cues in one call
        translate(resilient(small, logs), numbered(400))
        batches = [log.batch for log in logs if log.kind == "translate"]
        self.assertGreater(len(batches), 1)
        self.assertEqual(batches, sorted(batches))
        self.assertEqual(batches[0], 0)

    def test_a_retry_says_which_attempt_it_was_and_keeps_the_http_code(self):
        failures = [Unavailable("overloaded"), Unavailable("overloaded")]

        def handler(req, call_no):
            if failures:
                error = failures.pop(0)
                error.http_status = 503
                raise error
            return echo_items(req, "[vi] ")

        logs = []
        translate(resilient(FakeProvider(handler), logs), numbered(3))
        self.assertEqual([(log.outcome, log.retry, log.http_status) for log in logs],
                         [("unavailable", 0, 503), ("unavailable", 1, 503), ("ok", 2, 200)])
        self.assertEqual(logs[0].response_text, "")

    def test_the_gemini_mapping_keeps_the_http_code_on_the_typed_error(self):
        error = map_api_error(429, "RESOURCE_EXHAUSTED", "slow down", {})
        self.assertIsInstance(error, RateLimited)
        self.assertEqual(error.http_status, 429)
        self.assertEqual(map_api_error(503, None, "busy", {}).http_status, 503)

    def test_a_callback_that_raises_never_fails_the_call(self):
        def broken(_log):
            raise OSError("disk full")

        provider = ResilientProvider(FakeProvider(), on_call=broken)
        self.assertIsInstance(provider.generate(LLMRequest(system="s", user="u", meta={"targets": [1], "source": {1: "a"}})), LLMResponse)

    def test_the_finish_reason_is_passed_on(self):
        class Provider(FakeProvider):
            def generate(self, req):
                response = super().generate(req)
                response.finish_reason = "MAX_TOKENS"
                return response

        logs = []
        translate(resilient(Provider(), logs), numbered(2))
        self.assertEqual(logs[0].finish_reason, "MAX_TOKENS")


class ValidationIssueTests(unittest.TestCase):
    def test_three_missing_cues_are_three_issues_of_the_first_call(self):
        result = translate(FakeProvider(drop({3, 5, 7}, on_calls={1})), numbered(10))
        self.assertEqual(sorted((i.cue, i.kind, i.call, i.error) for i in result.issues),
                         [(3, "missing", "translate", True), (5, "missing", "translate", True), (7, "missing", "translate", True)])
        self.assertEqual(result.issues[0].source, "line 3")

    def test_a_clean_run_has_no_issues(self):
        self.assertEqual(translate(FakeProvider(), numbered(5)).issues, [])

    def test_a_line_count_warning_is_not_an_error(self):
        def handler(req, call_no):
            return [{"i": i, "text": "a\nb\nc"} for i in req.meta["targets"]]

        result = translate(FakeProvider(handler), numbered(2))
        self.assertTrue(result.issues)
        self.assertTrue(all(issue.kind == "lines" and not issue.error for issue in result.issues))

    def test_issues_survive_a_failed_translation(self):
        provider = FakeProvider(drop({2}))  # cue 2 is never answered
        with self.assertRaises(TranslationError) as raised:
            translate(provider, numbered(4))
        self.assertTrue(raised.exception.issues)
        self.assertEqual({issue.cue for issue in raised.exception.issues}, {2})
        self.assertEqual({issue.call for issue in raised.exception.issues} >= {"translate", "repair"}, True)


if __name__ == "__main__":
    unittest.main()
