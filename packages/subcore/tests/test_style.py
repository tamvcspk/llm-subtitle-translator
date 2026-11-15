"""Style profile: layers, cards, presets, rendering and the prompt (FR-18, criterion 16 a, e, f; ADR-14)."""

import json
import unittest

from subcore.bundle import TranslationBundle
from subcore.fewshot import estimate_tokens, select
from subcore.glossary import Glossary, Term, from_doc, to_doc, loads, dumps
from subcore.style import (
    PRESETS, CharacterCard, Style, StyleError, cards_present, language_style, render_style, render_voices,
    resolve_style, suggest_presets,
)
from subcore.translate import PromptContext, TranslateOptions
from subcore.translate.engine import make_bundle
from subcore.translate.prompt import render_system

from .helpers import numbered


def system(*, style=Style(), voices=(), glossary=None, few_shot=()):
    ctx = PromptContext(
        target_lang="vi", glossary=glossary, style=style, voices=list(voices), few_shot=list(few_shot),
    )
    return render_system(make_bundle(numbered(2, "Bob {n}"), ctx, TranslateOptions(), model="m"))


class LayerTests(unittest.TestCase):
    def test_the_highest_layer_wins_field_by_field(self):
        series = Style(formality="casual", tone="dry", max_cps=17)
        episode = Style(formality="formal")
        effective = resolve_style([language_style("vi"), series, episode])
        self.assertEqual(effective.formality, "formal")  # the episode beats the series
        self.assertEqual(effective.tone, "dry")  # nothing above says anything about it
        self.assertEqual(effective.max_cps, 17)
        self.assertEqual(effective.dialect, "neutral")  # the language is the floor
        self.assertEqual(effective.max_lines, 2)

    def test_unset_never_overrides_set(self):
        self.assertEqual(resolve_style([Style(profanity="keep"), Style()]).profanity, "keep")
        self.assertEqual(resolve_style([None, Style(profanity="soften"), None]).profanity, "soften")

    def test_notes_of_every_layer_are_kept_once(self):
        got = resolve_style([Style(notes="family sitcom"), Style(notes="a flashback"), Style(notes="family sitcom")])
        self.assertEqual(got.notes, "family sitcom\na flashback")

    def test_a_document_round_trips_and_names_the_bad_field(self):
        style = Style(formality="formal", tone="warm", max_cps=15, allow_condense=False, notes="x")
        self.assertEqual(Style.from_doc(style.to_doc()), style)
        self.assertEqual(Style().to_doc(), {})
        for bad, where in (
            ({"formality": "rude"}, "formality"), ({"max_cps": 0}, "max_cps"), ({"max_lines": "many"}, "max_lines"),
            ({"colour": "red"}, "colour"), ({"allow_condense": "yes"}, "allow_condense"), ([], "style"),
        ):
            with self.subTest(bad):
                with self.assertRaisesRegex(StyleError, where):
                    Style.from_doc(bad)

    def test_other_languages_have_no_language_layer(self):
        self.assertTrue(language_style("fr").is_empty())
        self.assertEqual(language_style("vi-VN"), language_style("vi"))


class CardTests(unittest.TestCase):
    CARDS = [
        CharacterCard("Bob", speech="fast and sarcastic", address=(("Alice", "anh–em"),), catchphrases=("No way",),
                      style=Style(formality="casual")),
        CharacterCard("Carol", aliases=("Caz",), speech="childish"),
        CharacterCard("Dan", always=True),
    ]

    def test_present_means_named_in_the_dialogue_or_always(self):
        names = [c.name for c in cards_present(self.CARDS, ["Hi Bob!", "see you, caz"])]
        self.assertEqual(names, ["Bob", "Carol", "Dan"])
        self.assertEqual([c.name for c in cards_present(self.CARDS, ["Nobody here"])], ["Dan"])
        self.assertEqual([c.name for c in cards_present(self.CARDS, ["Bobsled"])], ["Dan"])  # whole words only

    def test_a_card_round_trips(self):
        for card in self.CARDS:
            self.assertEqual(CharacterCard.from_doc(card.to_doc()), card)

    def test_a_bad_card_is_refused_with_its_field(self):
        for bad, where in (
            ({"name": ""}, "name"), ({"name": "X", "address": [{"to": "Y"}]}, "address"),
            ({"name": "X", "samples": "not a list"}, "samples"), ({"name": "X", "style": {"tone": 3, "formality": "x"}}, "style"),
        ):
            with self.subTest(bad):
                with self.assertRaisesRegex(StyleError, where):
                    CharacterCard.from_doc(bad)

    def test_criterion_16a_the_card_wins_over_the_series_in_the_prompt(self):
        series = Style(formality="formal")
        text = system(style=series, voices=[self.CARDS[0]])
        self.assertIn("Formality: formal register", text)  # the series says formal ...
        voices = text.split("Character voices")[1]
        self.assertIn("Formality: casual, spoken register", voices)  # ... and the card, listed after it, casual
        self.assertLess(text.index("Effective style"), text.index("Character voices"))
        self.assertIn("follow the character", voices)

    def test_criterion_16a_a_locked_term_still_wins_over_every_style_layer(self):
        glossary = Glossary(terms=(Term("Kyber", "tinh thể Kyber", locked=True),))
        text = system(style=Style(localization="adapt"), voices=[self.CARDS[0]], glossary=glossary)
        glossary_at, style_at = text.index("Glossary"), text.index("Effective style")
        self.assertLess(glossary_at, style_at)  # the authoritative section comes first
        self.assertIn("[LOCKED]", text)
        self.assertIn("the glossary still wins", text)


class RenderTests(unittest.TestCase):
    def test_an_empty_style_renders_nothing(self):
        self.assertEqual(render_style(Style()), "")
        self.assertEqual(render_voices([]), "")
        self.assertNotIn("Effective style", system())
        self.assertNotIn("Character voices", system())

    def test_limits_and_notes_are_rendered(self):
        text = render_style(Style(max_cps=17, max_line_length=42, max_lines=2, allow_condense=True, notes="a\nb"))
        self.assertIn("at most 17 characters per second", text)
        self.assertIn("lines of at most 42 characters", text)
        self.assertIn("at most 2 line(s)", text)
        self.assertIn("shorten wording", text)
        self.assertIn("- a\n- b", text)

    def test_the_style_is_in_the_bundle_and_its_hash(self):
        plain = make_bundle(numbered(2), PromptContext(), model="m")
        styled = make_bundle(numbered(2), PromptContext(style=Style(tone="dry")), model="m")
        self.assertNotEqual(plain.bundle_hash(), styled.bundle_hash())
        again = TranslationBundle.from_json(styled.to_json())
        self.assertEqual(again.style, Style(tone="dry"))
        self.assertEqual(again.bundle_hash(), styled.bundle_hash())
        self.assertEqual(render_system(again), render_system(styled))

    def test_voices_survive_a_round_trip_through_json(self):
        card = CharacterCard("Bob", speech="fast", address=(("Alice", "anh–em"),), style=Style(profanity="keep"))
        b = make_bundle(numbered(1), PromptContext(voices=[card]), model="m")
        self.assertEqual(TranslationBundle.from_json(b.to_json()).voices, (card,))
        self.assertEqual(json.loads(b.to_json())["voices"][0]["name"], "Bob")

    def test_a_bad_style_in_a_bundle_is_a_bundle_error_with_a_reason(self):
        data = json.loads(make_bundle(numbered(1), PromptContext(), model="m").to_json())
        data["style"] = {"formality": "rude"}
        from subcore.bundle import BundleError

        with self.assertRaisesRegex(BundleError, "style"):
            TranslationBundle.from_dict(data)


class PresetTests(unittest.TestCase):
    def test_every_preset_is_a_valid_style(self):
        self.assertEqual(set(PRESETS), {"sitcom", "drama", "anime", "documentary", "kids"})
        for preset in PRESETS.values():
            self.assertEqual(Style.from_doc(preset.style.to_doc()), preset.style)

    def test_criterion_16f_genres_suggest_presets(self):
        self.assertEqual(suggest_presets(["Comedy"]), ["sitcom"])
        self.assertEqual(suggest_presets(["Drama", "Crime"]), ["drama"])
        self.assertEqual(suggest_presets(["Documentary"]), ["documentary"])
        self.assertEqual(suggest_presets(["Animation", "Drama"]), ["anime", "drama"])  # the narrow one first
        self.assertEqual(suggest_presets(["comedy", " Family "]), ["kids", "sitcom"])
        self.assertEqual(suggest_presets(["Western", ""]), [])
        self.assertEqual(suggest_presets([]), [])


class GlossaryDocTests(unittest.TestCase):
    YAML = """
version: 1
target_lang: vi
style: legacy note
terms:
  - {source: Kyber crystal, target: tinh thể Kyber, aliases: [Kyber crystals], locked: true, from: "S01E03"}
characters:
  - {name: Alice, target: Alice, gender: f, description: lead}
address_rules:
  - {a: Alice, b: Bob, a_to_b: "em–anh", from: "S01E06 00:32:10", until: "S02E01", note: after}
"""

    def test_criterion_16g_a_document_is_the_yaml_as_a_mapping(self):
        glossary = loads(self.YAML)
        doc = to_doc(glossary)
        self.assertEqual(from_doc(doc), glossary)
        self.assertEqual(loads(dumps(from_doc(doc))), glossary)  # doc -> yaml -> glossary
        self.assertEqual(doc["terms"][0]["from"], "S01E03")

    def test_a_bad_document_names_its_path(self):
        with self.assertRaisesRegex(Exception, r"terms\[0\]"):
            from_doc({"terms": [{"source": "x"}]})
        with self.assertRaises(Exception):
            from_doc([])
        self.assertTrue(from_doc(None).is_empty())


class FewShotTests(unittest.TestCase):
    EDITS = [
        ("What a lovely day for a walk", "Hôm nay đẹp trời để đi dạo"),
        ("The dragon sleeps under the mountain", "Con rồng ngủ dưới núi"),
        ("Pass the salt please", "Đưa giúp tôi lọ muối"),
        ("walk the dragon", "dắt rồng đi dạo"),
    ]

    def test_the_closest_edits_come_first_and_unrelated_ones_never(self):
        got = select(self.EDITS, ["The dragon walks home"], 500)
        self.assertEqual(got[0][0], "walk the dragon")  # a shorter line sharing the rare word ranks first
        self.assertIn(("The dragon sleeps under the mountain", "Con rồng ngủ dưới núi"), got)
        self.assertNotIn("Pass the salt please", [s for s, _ in got])

    def test_the_budget_bounds_the_section(self):
        full = select(self.EDITS, ["dragon walk day"], 10_000)
        self.assertGreater(len(full), 1)
        budget = estimate_tokens(full[0][0]) + estimate_tokens(full[0][1]) + 4
        one = select(self.EDITS, ["dragon walk day"], budget)
        self.assertEqual(one, full[:1])
        self.assertEqual(select(self.EDITS, ["dragon walk day"], 0), ())

    def test_the_limit_and_the_order_are_deterministic(self):
        a = select(self.EDITS, ["dragon walk day"], 10_000, limit=2)
        self.assertEqual(len(a), 2)
        self.assertEqual(a, select(list(reversed(self.EDITS)), ["dragon walk day"], 10_000, limit=2))

    def test_nothing_to_compare_gives_nothing(self):
        self.assertEqual(select([], ["x y z"], 100), ())
        self.assertEqual(select(self.EDITS, ["the and"], 100), ())  # no word of four letters
        self.assertEqual(select([("", "x"), ("y", "")], ["anything"], 100), ())

    def test_examples_reach_the_prompt_after_the_metadata(self):
        text = system(few_shot=[("Bob walks", "Bob đi")])
        self.assertIn("Accepted translations of other lines", text)
        self.assertGreater(text.index("Examples"), text.index("Core rules"))


class GlossaryFilterTests(unittest.TestCase):
    """FR-4.3: past a threshold a request lists only the terms its lines use; address rules are never filtered."""

    @staticmethod
    def glossary(count):
        from subcore.glossary import AddressRule

        terms = tuple(Term(f"Alpha{n:03d}", f"A{n}", locked=True) for n in range(count))
        return Glossary(terms=terms, address_rules=(AddressRule("Ann", "Bo", a_to_b="em–anh"),))

    def bundle(self, count, threshold=50, text="Alpha007 meets Alpha013 at last"):
        ctx = PromptContext(target_lang="vi", glossary=self.glossary(count))
        return make_bundle(
            numbered(3, text), ctx, TranslateOptions(glossary_filter_threshold=threshold), model="m",
        )

    def test_a_glossary_over_the_threshold_lists_only_the_terms_the_lines_use(self):
        bundle = self.bundle(60)
        system = render_system(bundle, ["Alpha007 meets Alpha013 at last", "nothing else"])
        self.assertIn('"Alpha007"', system)
        self.assertIn('"Alpha013"', system)
        self.assertNotIn("Alpha000", system)
        self.assertNotIn("Alpha059", system)
        self.assertIn("Ann speaking to Bo", system)  # a pronoun-only scene still gets its form of address

    def test_a_scene_that_names_no_term_lists_none_but_keeps_the_address_rules(self):
        system = render_system(self.bundle(60), ["he said that she said"])
        self.assertNotIn("Alpha0", system)
        self.assertIn("Ann speaking to Bo", system)
        self.assertNotIn("Terms (a LOCKED", system)

    def test_at_or_under_the_threshold_and_with_zero_nothing_is_filtered(self):
        for count, threshold in ((50, 50), (60, 0)):
            with self.subTest(count=count, threshold=threshold):
                system = render_system(self.bundle(count, threshold), ["Alpha007 only"])
                self.assertIn("Alpha000", system)
                self.assertIn(f"Alpha{count - 1:03d}", system)

    def test_without_the_lines_of_a_request_the_whole_glossary_is_shown(self):
        self.assertIn("Alpha000", render_system(self.bundle(60)))

    def test_the_prompt_the_engine_sends_is_filtered_per_request_and_the_threshold_is_in_the_hash(self):
        from subcore.providers import FakeProvider
        from subcore.translate import run

        bundle = self.bundle(60)
        provider = FakeProvider()
        run(bundle, provider)
        self.assertNotIn("Alpha000", provider.calls[0].system)
        self.assertIn("Alpha007", provider.calls[0].system)
        self.assertNotEqual(bundle.bundle_hash(), self.bundle(60, threshold=0).bundle_hash())
        again = TranslationBundle.from_json(bundle.to_json())
        self.assertEqual(again.params.glossary_filter_threshold, 50)

    def test_the_cli_render_matches_what_would_be_sent(self):
        from subcore.translate.engine import render_requests

        requests = render_requests(self.bundle(60))
        self.assertNotIn("Alpha000", requests[0].system)
        self.assertIn("Alpha013", requests[0].system)


if __name__ == "__main__":
    unittest.main()
