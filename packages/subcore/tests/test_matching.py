"""One answer to "does this line mention that name?" (R12-2, R12-10): CJK and inflected names are found, a short name
stays strict, and no other module writes a word-boundary pattern."""

import re
import unittest
from pathlib import Path

import subcore
from subcore import matching
from subcore.bundle import matches
from subcore.glossary import Glossary, Term, loads as load_glossary, locked_violations
from subcore.providers import FakeProvider
from subcore.style import CharacterCard, cards_present
from subcore.translate import PromptContext, TranslateOptions
from subcore.translate.engine import compose_request, make_bundle, render_requests, run
from subcore.translate.prompt import render_system
from subcore.providers.base import Capabilities

from .helpers import make_cues, numbered


class ContainsTests(unittest.TestCase):
    def test_names_in_text_without_spaces_are_found(self):
        self.assertTrue(matching.contains("田中さんが来た", "田中"))  # Japanese: no space before the particle
        self.assertTrue(matching.contains("我看见了张伟。", "张伟"))  # Chinese
        self.assertTrue(matching.contains("철수가 왔다", "철수"))  # Korean: the particle sticks to the noun
        self.assertTrue(matching.contains("สมชายมาแล้ว", "สมชาย"))  # Thai

    def test_a_name_is_a_whole_word_with_room_for_an_ending(self):
        self.assertTrue(matching.contains("The Hobbits sleep.", "Hobbit"))  # plural
        self.assertTrue(matching.contains("Bilbo's hole", "Bilbo"))  # possessive
        self.assertTrue(matching.contains("Gandalfa pozdrav", "Gandalf"))  # a case ending
        self.assertTrue(matching.contains("the HOBBIT", "hobbit"))  # case ignored
        self.assertFalse(matching.contains("Hobbitual", "Hobbit"))  # three letters more is another word

    def test_a_short_name_stays_strict(self):
        self.assertTrue(matching.contains("Tom said so", "Tom"))
        self.assertFalse(matching.contains("Tommy said so", "Tom"))
        self.assertFalse(matching.contains("tomorrow", "Tom"))

    def test_an_empty_name_is_never_mentioned(self):
        self.assertFalse(matching.contains("anything", ""))
        self.assertFalse(matching.contains("anything", "  "))

    def test_a_name_with_regex_characters_is_taken_literally(self):
        self.assertTrue(matching.contains("see C++ (again) now", "C++ (again)"))
        self.assertFalse(matching.contains("see CC again", "C++"))


class OneMatcherTests(unittest.TestCase):
    """The four callers agree, because they ask the same function."""

    GLOSSARY = load_glossary(
        "version: 1\nterms:\n  - {source: 田中, target: Tanaka, locked: true}\n  - {source: Hobbit, target: Hobbit, locked: true}\n"
    )

    def test_a_locked_cjk_term_is_demanded_of_the_translation(self):
        self.assertEqual(
            [t.source for t in locked_violations(self.GLOSSARY, "田中さんが来た", "Anh ấy đến")], ["田中"])
        self.assertEqual(locked_violations(self.GLOSSARY, "田中さんが来た", "Tanaka đến"), [])

    def test_the_inflected_locked_term_is_demanded_too(self):
        self.assertEqual([t.source for t in locked_violations(self.GLOSSARY, "The Hobbits run", "Họ chạy")], ["Hobbit"])

    def test_a_character_card_is_found_by_a_cjk_name(self):
        card = CharacterCard(name="Tanaka", aliases=("田中",), speech="polite")
        self.assertEqual(cards_present([card], ["田中さんが来た"]), [card])
        self.assertEqual(cards_present([card], ["山田さんが来た"]), [])

    def test_the_console_matches_a_cue_by_a_cjk_name(self):
        bundle = make_bundle(make_cues(["田中さんが来た", "誰もいない"]), PromptContext(target_lang="vi", glossary=self.GLOSSARY))
        hits = {m.label: m.cues for m in matches(bundle, 1, 2)}
        self.assertEqual(hits["田中 → Tanaka"], (1,))

    def test_a_big_glossary_lists_the_locked_cjk_and_inflected_terms_its_lines_mention(self):
        terms = [Term(source="田中", target="Tanaka", locked=True), Term(source="Hobbit", target="Hobbit", locked=True)]
        terms += [Term(source=f"Filler{n:03d}", target=f"F{n}") for n in range(60)]
        glossary = Glossary(terms=tuple(terms))
        bundle = make_bundle(
            make_cues(["田中さんが来た", "The Hobbits run"]),
            PromptContext(target_lang="vi", glossary=glossary), TranslateOptions(glossary_filter_threshold=50), model="m",
        )
        system = render_system(bundle, ["田中さんが来た", "The Hobbits run"])
        self.assertIn('"田中"', system)
        self.assertIn('"Hobbit"', system)
        self.assertNotIn("Filler000", system)


class RequestsMatchTests(unittest.TestCase):
    def test_every_first_request_of_a_part_is_the_one_render_requests_shows(self):
        """R12-4: several parts, a glossary above the filter threshold, a kept cue: shown = sent, system and user."""
        terms = tuple(Term(source=f"Word{n:03d}", target=f"W{n}") for n in range(70)) + (
            Term(source="line 7", target="dòng bảy", locked=True),
        )
        ctx = PromptContext(target_lang="vi", glossary=Glossary(terms=terms), title="Show")
        provider = FakeProvider(caps=Capabilities(max_output_tokens=2000))
        bundle = make_bundle(numbered(300), ctx, TranslateOptions(), keep={5: "năm"})
        shown = render_requests(bundle, provider)
        self.assertGreater(len(shown), 1)
        run(bundle, provider)
        firsts = {}
        for call in provider.calls:
            if call.meta["kind"] == "translate":
                firsts.setdefault(call.meta["batch"], call)
        self.assertEqual(sorted(firsts), [r.part for r in shown])
        for request in shown:
            sent = firsts[request.part]
            self.assertEqual((sent.system, sent.user), (request.system, request.user))
        self.assertIn("line 7", shown[0].system)
        self.assertNotIn("Word000", shown[0].system)

    def test_compose_request_is_what_both_paths_use(self):
        bundle = make_bundle(numbered(4), PromptContext(target_lang="vi"), keep={2: "hai"})
        source = {n: c.text for n, c in enumerate(bundle.cues, start=1)}
        system, user = compose_request(bundle, source, [1, 3, 4])
        self.assertEqual(system, render_system(bundle, source.values()))
        self.assertIn("ALREADY TRANSLATED", user)


class OnePlaceTests(unittest.TestCase):
    def test_only_the_matching_module_writes_a_word_boundary_pattern(self):
        """R12-10: a fix to the rule lands in one place. A second ``(?<!\\w)`` anywhere else is the old drift again."""
        root = Path(subcore.__file__).parent
        offenders = [
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if path.name != "matching.py" and re.search(r"\(\?<!\\w\)", path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
