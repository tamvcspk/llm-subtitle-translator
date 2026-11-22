"""What an admin keeps correcting, and the golden set made of those corrections (FR-19.6; criteria 20a, 20b)."""

import json
import unittest
from pathlib import Path

from subcore import compare, feedback, golden
from subcore.feedback import EditedCue, find_patterns, replacements
from subcore.translate.engine import CallRecord, TranslationResult

from .helpers import make_cues, numbered
from .test_bundle_cli import CliTestBase, run_cli


def edit(key, source, before, after, **kw):
    return EditedCue(key, source, before, after, **kw)


MOM = [
    edit(1, "Mom, wait!", "Mẹ ơi, đợi đã!", "Má ơi, đợi đã!"),
    edit(2, "I love you, Mom.", "Con yêu mẹ.", "Con yêu má."),
    edit(3, "Where is Mom?", "Mẹ đâu rồi?", "Má đâu rồi?"),
    edit(4, "The door is open.", "Cửa mở rồi.", "Cánh cửa mở rồi."),
]


class ReplacementTests(unittest.TestCase):
    def test_a_word_swapped_for_another_is_found_as_written(self):
        self.assertEqual(replacements("Mẹ ơi, đợi đã!", "Má ơi, đợi đã!"), [("Mẹ", "Má")])

    def test_a_phrase_for_a_phrase_up_to_three_words(self):
        self.assertEqual(replacements("Con rất yêu mẹ", "Con thương má lắm"), [("rất yêu mẹ", "thương má lắm")])

    def test_a_word_added_or_taken_out_is_no_swap(self):
        self.assertEqual(replacements("Cửa mở rồi.", "Cánh cửa mở rồi."), [])
        self.assertEqual(replacements("Cửa mở rồi.", "Cửa mở."), [])
        self.assertEqual(replacements("Cửa mở.", "Cửa mở rồi."), [])

    def test_a_change_of_capitals_or_punctuation_alone_is_no_swap(self):
        self.assertEqual(replacements("mẹ ơi!", "Mẹ ơi."), [])

    def test_a_long_rewrite_is_no_swap(self):
        self.assertEqual(replacements("a b c d e", "v w x y z"), [])


class PatternTests(unittest.TestCase):
    def test_20a_the_same_swap_in_three_cues_is_a_pattern_with_its_source_word(self):
        (pattern,) = find_patterns(MOM)
        self.assertEqual((pattern.old, pattern.new, pattern.count), ("Mẹ", "Má", 3))
        self.assertEqual(pattern.source, "mom")
        self.assertEqual({c.key for c in pattern.cues}, {1, 2, 3})

    def test_two_cues_are_not_enough(self):
        self.assertEqual(find_patterns(MOM[:2]), [])

    def test_a_cue_counts_once_however_often_the_swap_shows_in_it(self):
        twice = edit(9, "Mom, where is Mom?", "Mẹ ơi, mẹ đâu?", "Má ơi, má đâu?")
        self.assertEqual(find_patterns([twice, MOM[0], MOM[1]])[0].count, 3)

    def test_case_and_spacing_do_not_split_a_pattern(self):
        cues = [edit(1, "Mom!", "mẹ ơi", "má ơi"), edit(2, "Mom?", "Mẹ  ơi", "Má ơi"), edit(3, "Mom.", "MẸ ơi", "MÁ ơi")]
        self.assertEqual(find_patterns(cues)[0].count, 3)

    def test_the_source_word_must_be_in_every_cue_and_rare_among_the_others(self):
        # "the" is in every pattern cue, but also in every other edited cue: it says nothing.
        pattern_cues = [edit(i, f"The Mom {i}", "Mẹ", "Má") for i in range(3)]
        others = [edit(10 + i, "The door", "Cửa", "Cánh cửa") for i in range(4)]
        self.assertEqual(find_patterns(pattern_cues + others)[0].source, "mom")

    def test_no_shared_source_word_gives_a_pattern_without_one(self):
        cues = [edit(1, "Mum!", "Mẹ", "Má"), edit(2, "Mommy!", "Mẹ", "Má"), edit(3, "Mother!", "Mẹ", "Má")]
        self.assertEqual(find_patterns(cues)[0].source, "")

    def test_the_most_repeated_pattern_comes_first(self):
        more = [edit(20 + i, f"Dad {i}", "Bố", "Ba") for i in range(4)]
        found = find_patterns(MOM + more)
        self.assertEqual([(p.old, p.count) for p in found], [("Bố", 4), ("Mẹ", 3)])


class GoldenFileTests(unittest.TestCase):
    CUES = [
        golden.GoldenCue(1, 2, 3, "Mom, wait!", "Má ơi, đợi đã!"),
        golden.GoldenCue(1, 1, 1, "Hello", "Chào"),
        golden.GoldenCue(None, None, 2, "Movie line", "Câu phim"),
    ]

    def test_round_trip_and_stable_order(self):
        text = golden.dumps(self.CUES, series="Show", lang="vi")
        self.assertEqual(golden.loads(text), sorted(self.CUES, key=lambda c: (c.season if c.season is not None else -1, c.episode or 0, c.cue)))
        self.assertEqual(text, golden.dumps(list(reversed(self.CUES)), series="Show", lang="vi"))
        self.assertEqual(json.loads(text)["golden_version"], 1)

    def test_a_bad_file_says_what_is_wrong(self):
        for text, word in [("nope", "not JSON"), ("[]", "cues"), ('{"cues": [1]}', "entry 1"),
                           ('{"cues": [{"cue": "3", "source": "a", "text": "b"}]}', "whole number"),
                           ('{"golden_version": 9, "cues": []}', "version"),
                           ('{"cues": [{"cue": 0, "source": "a", "text": "b"}]}', "1 or more")]:
            with self.assertRaises(golden.GoldenError, msg=text) as caught:
                golden.loads(text)
            self.assertIn(word, str(caught.exception))

    def test_for_episode_keeps_the_cues_whose_source_is_unchanged(self):
        source = make_cues(["Hello", "Mom,  wait!"])
        entries = [golden.GoldenCue(1, 2, 2, "Mom, wait!", "Má ơi"), golden.GoldenCue(1, 2, 1, "Other line", "x"),
                   golden.GoldenCue(1, 3, 1, "Hello", "y"), golden.GoldenCue(1, 2, 9, "Nowhere", "z")]
        self.assertEqual(golden.for_episode(entries, 1, 2, source), {2: "Má ơi"})  # spacing is not a change


class GoldenScoreTests(unittest.TestCase):
    @staticmethod
    def result(texts):
        cues = make_cues(texts)
        return TranslationResult(cues=cues, calls=[CallRecord("translate", len(cues))])

    def test_20b_a_mapping_scores_only_the_corrected_cues_and_tells_two_configurations_apart(self):
        wanted = {2: "Má ơi", 4: "Cánh cửa"}
        old = self.result(["a", "Mẹ ơi", "c", "Cửa", "e"])  # the old prompt keeps the machine's words
        new = self.result(["a", "Má ơi", "c", "Cánh cửa", "e"])  # the new one got both right
        scored_old = compare.summarize("old", old, [], golden=wanted)
        scored_new = compare.summarize("new", new, [], golden=wanted)
        self.assertEqual((scored_old.golden_exact, scored_new.golden_exact), (0, 2))
        self.assertLess(scored_old.golden_similarity, scored_new.golden_similarity)
        self.assertEqual(scored_new.golden_similarity, 1.0)

    def test_a_sequence_still_scores_every_cue(self):
        scored = compare.summarize("x", self.result(["a", "b"]), [], golden=["a", "z"])
        self.assertEqual(scored.golden_exact, 1)


class CompareGoldenJsonTests(CliTestBase):
    def setUp(self):
        super().setUp()
        self.cues = numbered(5, "Kyber crystal {n} glows")
        self.bundle = self.path("a.json")
        run_cli("bundle", str(self.src), "--season", "1", "--episode", "2", "-o", self.bundle)
        self.golden = self.dir / "golden.json"
        entries = [golden.GoldenCue(1, 2, n, self.cues[n - 1].text, f"[vi] {self.cues[n - 1].text}") for n in (2, 4)]
        entries.append(golden.GoldenCue(1, 3, 1, self.cues[0].text, "another episode"))
        self.golden.write_text(golden.dumps(entries, series="Show", lang="vi"), encoding="utf-8")

    def test_compare_scores_the_corrected_cues_of_this_episode_only(self):
        code, out, err = run_cli("compare", str(self.src), "--bundle", self.bundle, "--golden", str(self.golden),
                                 "--provider", "fake")
        self.assertEqual(code, 0, err)
        row = next(line for line in out.splitlines() if line.startswith("golden: exact"))
        self.assertTrue(row.split()[-1] == "2", row)  # cues 2 and 4: the entry of episode 3 is not scored

    def test_20b_two_configurations_get_different_scores_against_the_golden_set(self):
        other = self.path("b.json")
        run_cli("bundle", str(self.src), "--season", "1", "--episode", "2", "--target", "fr", "-o", other)
        code, out, err = run_cli("compare", str(self.src), "--bundle", self.bundle, "--bundle", other,
                                 "--golden", str(self.golden), "--provider", "fake")
        self.assertEqual(code, 0, err)
        exact = next(line for line in out.splitlines() if line.startswith("golden: exact")).split()
        self.assertEqual(exact[-2:], ["2", "0"])

    def test_an_episode_without_a_corrected_cue_fails_before_any_call(self):
        elsewhere = self.path("c.json")
        run_cli("bundle", str(self.src), "--season", "7", "--episode", "7", "-o", elsewhere)
        code, _, err = run_cli("compare", str(self.src), "--bundle", elsewhere, "--golden", str(self.golden),
                               "--provider", "fake")
        self.assertEqual(code, 2)
        self.assertIn("no corrected cue", err)

    def test_a_broken_golden_file_is_an_error_not_a_traceback(self):
        bad = self.dir / "bad.json"
        bad.write_text("{", encoding="utf-8")
        code, _, err = run_cli("compare", str(self.src), "--bundle", self.bundle, "--golden", str(bad), "--provider", "fake")
        self.assertEqual(code, 2)
        self.assertIn("not JSON", err)


class OnePlaceTests(unittest.TestCase):
    def test_feedback_and_golden_are_pure(self):
        for module in (feedback, golden):
            text = Path(module.__file__).read_text(encoding="utf-8")
            for forbidden in ("sqlite3", "subserver", "urllib", "socket"):
                self.assertNotIn(forbidden, text, module.__name__)


if __name__ == "__main__":
    unittest.main()
