"""The LLM judge between two translations of a cue (FR-18.8, FR-19.6; P15). A fake provider answers; nothing is called."""

import json
import unittest
from pathlib import Path

from subcore import judge
from subcore.judge import BATCH, JudgeError, Pair, Verdict, batches, variant_is_a
from subcore.providers import BadOutput, Capabilities, FakeProvider, RateLimited


def pair(n, **kw):
    fields = dict(source=f"Source {n}", current=f"Hiện tại {n}", variant=f"Biến thể {n}")
    return Pair(n, **{**fields, **kw})


def answer(*entries):
    return json.dumps({"verdicts": list(entries)})


def items_of(req):
    return [json.loads(line) for line in req.user.split("\n")[1:]]


def always(better):
    """A model that says ``better`` for every item it is shown."""
    return lambda req, call_no: answer(*({"i": i, "better": better, "why": "x"} for i in range(len(items_of(req)))))


class OrderTests(unittest.TestCase):
    def test_the_order_is_fixed_by_the_cue_and_well_mixed(self):
        self.assertEqual([variant_is_a(n) for n in range(1, 40)], [variant_is_a(n) for n in range(1, 40)])
        first = sum(variant_is_a(n) for n in range(1, 401))
        self.assertTrue(160 <= first <= 240, first)  # neither side is nearly always first
        # not alternating either: a run of equal neighbours exists, so "even cues only" cannot be always B
        self.assertTrue(any(variant_is_a(n) == variant_is_a(n + 1) for n in range(1, 40)))

    def test_what_a_cue_is_shown_as_does_not_depend_on_the_others_asked_about(self):
        for n in (3, 4, 17):
            fake = FakeProvider(always("A"))
            alone = judge.judge(fake, [pair(n)], lang="vi")
            crowd = judge.judge(fake, [pair(1), pair(n), pair(99)], lang="vi")
            self.assertEqual(alone[0], next(v for v in crowd if v.n == n))


class AskTests(unittest.TestCase):
    def test_the_request_carries_texts_only_and_the_context_line(self):
        fake = FakeProvider(always("tie"))
        judge.judge(fake, [pair(7, before="Where were you?"), pair(8)], lang="vi")
        (req,) = fake.calls
        self.assertEqual((req.task, req.json_output, req.meta["kind"]), ("judge", True, "judge"))
        self.assertIn("`vi`", req.system)
        first, second = items_of(req)
        self.assertEqual([first["i"], second["i"]], [0, 1])  # a place in the call, not a cue number
        self.assertEqual(first["prev"], "Where were you?")
        self.assertNotIn("prev", second)
        self.assertEqual({first["A"], first["B"]}, {"Hiện tại 7", "Biến thể 7"})
        self.assertEqual(first["source"], "Source 7")
        self.assertNotIn("-->", req.user)  # no timecode ever goes to the model
        self.assertEqual(set(first), {"i", "source", "A", "B", "prev"})
        self.assertIn("Do not prefer a translation because it is longer", req.system)
        self.assertIn('answer "tie"', req.system)

    def test_the_variant_is_shown_first_exactly_where_the_order_says(self):
        fake = FakeProvider(always("A"))
        pairs = [pair(n) for n in range(1, 21)]
        verdicts = judge.judge(fake, pairs, lang="vi")
        for item, verdict in zip(items_of(fake.calls[0]), verdicts):
            self.assertEqual(item["A"] == f"Biến thể {verdict.n}", variant_is_a(verdict.n))
            self.assertEqual(verdict.winner, "variant" if variant_is_a(verdict.n) else "current")

    def test_b_means_the_other_side_and_a_tie_is_a_tie(self):
        pairs = [pair(n) for n in range(1, 21)]
        for verdict in judge.judge(FakeProvider(always("B")), pairs, lang="vi"):
            self.assertEqual(verdict.winner, "current" if variant_is_a(verdict.n) else "variant")
        self.assertEqual({v.winner for v in judge.judge(FakeProvider(always("tie")), pairs, lang="vi")}, {"tie"})

    def test_the_answer_is_read_loosely_where_it_is_safe(self):
        entries = [{"i": 0, "better": " a. "}, {"i": 1, "better": "TIE"}, {"i": "2", "better": "b", "why": "  two\n words  "}]
        fake = FakeProvider(lambda req, n: "```json\n" + answer(*entries) + "\n```")
        verdicts = judge.judge(fake, [pair(1), pair(2), pair(3)], lang="vi")
        self.assertEqual([v.n for v in verdicts], [1, 2, 3])
        self.assertEqual(verdicts[1].winner, "tie")
        self.assertEqual(verdicts[2].why, "two words")

    def test_an_entry_that_is_not_what_was_asked_is_dropped_never_guessed(self):
        junk = [
            {"i": 0, "better": "A", "why": "ok"}, {"i": 0, "better": "B"},  # the first answer for an item wins
            {"i": 1, "better": "maybe"}, {"i": 2}, {"i": 9, "better": "A"}, {"i": -1, "better": "A"},
            "x", 5, {"better": "A"}, {"i": None, "better": "A"},
        ]
        verdicts = judge.judge(FakeProvider(lambda req, n: answer(*junk)), [pair(1), pair(2), pair(3)], lang="vi")
        self.assertEqual([(v.n, v.why) for v in verdicts], [(1, "ok")])

    def test_a_pair_the_model_skipped_has_no_verdict(self):
        fake = FakeProvider(lambda req, n: answer({"i": 1, "better": "tie"}))
        self.assertEqual([v.n for v in judge.judge(fake, [pair(1), pair(2)], lang="vi")], [2])

    def test_a_long_reason_is_cut(self):
        fake = FakeProvider(lambda req, n: answer({"i": 0, "better": "A", "why": "w " * 200}))
        (verdict,) = judge.judge(fake, [pair(1)], lang="vi")
        self.assertLessEqual(len(verdict.why), 120)

    def test_nothing_to_judge_asks_nothing(self):
        fake = FakeProvider(always("A"))
        self.assertEqual(judge.judge(fake, [], lang="vi"), [])
        self.assertEqual(fake.calls, [])

    def test_a_schema_goes_only_to_a_provider_that_takes_one(self):
        with_schema = FakeProvider(always("A"))
        without = FakeProvider(always("A"), caps=Capabilities(structured_output=False))
        judge.judge(with_schema, [pair(1)], lang="vi")
        judge.judge(without, [pair(1)], lang="vi")
        self.assertEqual(with_schema.calls[0].response_schema["required"], ["verdicts"])
        self.assertIsNone(without.calls[0].response_schema)

    def test_one_call_holds_at_most_a_batch(self):
        with self.assertRaises(ValueError):
            judge.judge(FakeProvider(always("A")), [pair(n) for n in range(1, BATCH + 2)], lang="vi")
        cut = batches([pair(n) for n in range(1, 2 * BATCH + 6)])
        self.assertEqual([len(b) for b in cut], [BATCH, BATCH, 5])
        self.assertEqual([p.n for b in cut for p in b], list(range(1, 2 * BATCH + 6)))
        self.assertEqual(batches([]), [])


class FailureTests(unittest.TestCase):
    def test_garbage_twice_is_a_judge_error_after_two_calls(self):
        fake = FakeProvider(lambda req, n: "I think A is better")
        with self.assertRaises(JudgeError):
            judge.judge(fake, [pair(1)], lang="vi")
        self.assertEqual(len(fake.calls), 2)

    def test_an_unusable_first_answer_is_asked_again(self):
        def handler(req, call_no):
            if call_no == 1:
                raise BadOutput("empty")
            return answer({"i": 0, "better": "tie"})

        fake = FakeProvider(handler)
        self.assertEqual(judge.judge(fake, [pair(1)], lang="vi"), [Verdict(1, "tie", "")])
        self.assertEqual(len(fake.calls), 2)

    def test_provider_errors_pass_through_typed(self):
        def handler(req, call_no):
            raise RateLimited("slow down", retry_after=3)

        with self.assertRaises(RateLimited):
            judge.judge(FakeProvider(handler), [pair(1)], lang="vi")


class PurityTests(unittest.TestCase):
    def test_the_judge_knows_no_worker_and_no_network(self):
        text = Path(judge.__file__).read_text(encoding="utf-8")
        for forbidden in ("sqlite3", "subserver", "urllib", "socket", "requests"):
            self.assertNotIn(forbidden, text)

    def test_the_prompt_version_is_a_label_a_verdict_can_carry(self):
        self.assertRegex(judge.JUDGE_PROMPT_VERSION, r"^j\d+$")


if __name__ == "__main__":
    unittest.main()
