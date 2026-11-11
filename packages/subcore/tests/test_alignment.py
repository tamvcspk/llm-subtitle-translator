"""Drift detection: a model that attaches text to the wrong cue number (seen with a lite model)."""

import unittest

from subcore.providers import FakeProvider
from subcore.translate import translate
from subcore.translate.alignment import anchors_of, find_misaligned

from .helpers import make_cues


def texts(n: int) -> list[str]:
    # odd cues carry a number (anchor), even cues carry none
    return [f"Door number {i * 13} is closed" if i % 2 else f"Nothing to see here {'x' * i}" for i in range(1, n + 1)]


def shifted(start: int, by: int = 2, only_first_call: bool = True):
    """Handler: from cue ``start`` on, answer with the text of cue ``i - by``."""

    def handler(req, call_no):
        source = req.meta["source"]
        items = []
        for i in req.meta["targets"]:
            wrong = i >= start and (call_no == 1 or not only_first_call) and (i - by) in source
            items.append({"i": i, "text": "[vi] " + source[i - by if wrong else i]})
        return items

    return handler


class AnchorUnitTests(unittest.TestCase):
    def test_numbers_are_normalised_and_tags_ignored(self):
        self.assertEqual(anchors_of('<font color="#ff0000">It costs $6,000</font>'), {"d:6000"})
        self.assertEqual(anchors_of("Chỉ 6.000 đô"), {"d:6000"})

    def test_names_count_only_mid_sentence(self):
        self.assertEqual(anchors_of("Yes. Thank you, Charles, and Rosa's team."), {"n:Charles", "n:Rosa"})

    def test_aligned_reply_has_no_findings(self):
        source = dict(enumerate(texts(20), start=1))
        out = {n: "[vi] " + t for n, t in source.items()}
        self.assertEqual(find_misaligned(list(source), source, out), {})

    def test_numbers_that_vanish_without_a_neighbour_are_not_drift(self):
        source = {1: "The 1st door", 2: "The 2nd door", 3: "The 3rd door", 4: "Another 4 doors"}
        out = {1: "Cửa thứ nhất", 2: "Cửa thứ hai", 3: "Cửa thứ ba", 4: "Thêm bốn cửa"}
        self.assertEqual(find_misaligned([1, 2, 3, 4], source, out), {})

    def test_a_single_mismatch_is_not_enough(self):
        source = dict(enumerate(texts(10), start=1))
        out = {n: "[vi] " + t for n, t in source.items()}
        out[5] = out[3]  # one cue carries its neighbour's text
        self.assertEqual(find_misaligned(list(source), source, out), {})

    def test_shift_flags_the_whole_tail_including_cues_without_anchors(self):
        source = dict(enumerate(texts(40), start=1))
        out = {n: "[vi] " + source[n if n < 11 else n - 2] for n in source}
        flagged = find_misaligned(list(source), source, out)
        self.assertTrue(set(range(12, 41)).issubset(flagged))
        self.assertFalse(set(range(1, 9)) & set(flagged))


def plain_texts(n: int) -> list[str]:
    # no digits, no mid-sentence names: only the length profile can reveal a shift
    return ["word " * (1 + (i * 7) % 11) + "end" for i in range(1, n + 1)]


class LengthProfileTests(unittest.TestCase):
    def test_shift_without_any_anchor_is_caught(self):
        source = dict(enumerate(plain_texts(60), start=1))
        out = {n: "[vi] " + source[n if n < 21 else n - 1] for n in source}
        flagged = find_misaligned(list(source), source, out)
        self.assertTrue(set(range(31, 61)).issubset(flagged))
        self.assertFalse(set(range(1, 11)) & set(flagged))

    def test_aligned_translation_with_uneven_ratio_is_not_flagged(self):
        source = dict(enumerate(plain_texts(60), start=1))
        out = {n: "xin chào " * 2 + t[: max(3, len(t) * 9 // 10)] for n, t in source.items()}
        self.assertEqual(find_misaligned(list(source), source, out), {})

    def test_uniform_lengths_carry_no_signal(self):
        source = {n: "same length text" for n in range(1, 41)}
        out = {n: "cùng độ dài" for n in range(1, 41)}
        self.assertEqual(find_misaligned(list(source), source, out), {})

    def test_engine_repairs_a_shift_with_no_anchors(self):
        cues = make_cues(plain_texts(60))
        provider = FakeProvider(shifted(21, by=1))
        result = translate(provider, cues)
        self.assertEqual([c.kind for c in result.calls], ["translate", "repair"])
        self.assertTrue(set(range(31, 61)).issubset(provider.calls[1].meta["targets"]))
        self.assertEqual([c.text for c in result.cues], ["[vi] " + t for t in plain_texts(60)])


class AnchorEngineTests(unittest.TestCase):
    def test_shift_costs_one_repair_over_the_shifted_region_and_is_fixed(self):
        cues = make_cues(texts(40))
        provider = FakeProvider(shifted(11))
        result = translate(provider, cues)

        self.assertEqual([c.kind for c in result.calls], ["translate", "repair"])
        repaired = provider.calls[1].meta["targets"]
        self.assertTrue(set(range(12, 41)).issubset(repaired))
        self.assertFalse(set(range(1, 9)) & set(repaired))  # verified cues are left alone
        self.assertIn("shifted", provider.calls[1].meta["notes"][repaired[0]])
        self.assertEqual([c.text for c in result.cues], ["[vi] " + t for t in texts(40)])
        self.assertEqual(result.warnings, [])

    def test_persistent_shift_warns_but_does_not_fail_the_job(self):
        cues = make_cues(texts(40))
        provider = FakeProvider(shifted(11, only_first_call=False))
        result = translate(provider, cues)

        self.assertEqual(len(result.cues), 40)
        self.assertTrue(any("neighbouring cue" in w for w in result.warnings))
        self.assertLessEqual(len(provider.calls), 3)  # translate + at most the two repair rounds

    def test_clean_reply_makes_exactly_one_call(self):
        provider = FakeProvider()
        translate(provider, make_cues(texts(40)))
        self.assertEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
