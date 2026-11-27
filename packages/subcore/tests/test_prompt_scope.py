"""A prompt without noise: forms of address only for the people of the episode, and the glossary wins (P19c, FR-5.11, FR-5.12)."""

import unittest
from dataclasses import replace

from subcore import srt
from subcore.bundle import BUNDLE_VERSION, TranslationBundle
from subcore.glossary import ActiveRule, AddressRule, Character, Glossary, dumps, loads
from subcore.memory import RELATIONSHIP_TITLE, UnitFacts, build_context, drop_pairs, Facts
from subcore.style import CharacterCard
from subcore.translate import PromptContext
from subcore.translate.engine import make_bundle
from subcore.translate.prompt import PROMPT_VERSION, render_system
from subcore.translate.scope import Names, ruled_pairs, scoped_rules, without_ruled_addresses


def rule(a, b, a_to_b="", b_to_a=""):
    return ActiveRule(AddressRule(a=a, b=b, a_to_b=a_to_b, b_to_a=b_to_a))


def glossary(*rules, characters=()):
    return Glossary(characters=tuple(characters), address_rules=tuple(r.rule for r in rules))


def bundle(rules, cast=(), sections=(), voices=(), characters=()):
    g = glossary(*rules, characters=characters)
    ctx = PromptContext(
        target_lang="vi", glossary=g, active_rules=list(rules), cast=list(cast), extra_sections=list(sections),
        voices=list(voices),
    )
    return make_bundle([srt.Cue(1, 0, 1000, "Hi", "00:00:00,000 --> 00:00:01,000")], ctx)


class NamesTests(unittest.TestCase):
    def test_same_person(self):
        names = Names(Glossary(characters=(Character("Caroline Miller", aliases=("Carolyn Miller", "Miss Miller")),)))
        self.assertTrue(names.same("lois", "Lois"))
        self.assertTrue(names.same("Lois", "Lois Wilkerson"))
        self.assertTrue(names.same("Carolyn Miller", "Caroline Miller"))  # a declared alias
        self.assertTrue(names.same("Miss Miller", "Caroline Miller"))
        self.assertFalse(names.same("Hal", "Hank"))
        self.assertFalse(names.same("Lois", "Lou"))
        self.assertFalse(names.same("Al", "Alice"))  # two letters is not a name to match by words

    def test_two_people_with_a_shared_first_name_among_a_known_cast_are_not_told_apart_by_guesswork(self):
        self.assertTrue(Names().same("Ida", "Ida Smith"))  # one name is the words of the other: the same person


class ScopeTests(unittest.TestCase):
    def test_no_cast_keeps_every_rule(self):
        rules = [rule("Malcolm", "Lois", "con–mẹ"), rule("Ned", "Ruth", "anh–chị")]
        self.assertEqual(scoped_rules(rules, (), glossary(*rules)), rules)

    def test_a_rule_stays_only_when_both_people_are_in_the_cast(self):
        rules = [rule("Malcolm", "Lois", "con–mẹ"), rule("Malcolm", "Ned", "tôi–ông"), rule("Ned", "Ruth", "anh–chị")]
        kept = scoped_rules(rules, ["Malcolm", "Lois Wilkerson", "Ned"], glossary(*rules))
        self.assertEqual([(r.rule.a, r.rule.b) for r in kept], [("Malcolm", "Lois"), ("Malcolm", "Ned")])

    def test_the_prompt_lists_forms_of_address_for_the_cast_only(self):
        rules = [rule("Malcolm", "Lois", "con–mẹ"), rule("Ned", "Ruth", "anh–chị")]
        text = render_system(bundle(rules, cast=["Malcolm", "Lois"]))
        self.assertIn("Malcolm speaking to Lois", text)
        self.assertNotIn("Ned speaking to Ruth", text)

    def test_a_character_the_cast_misses_keeps_its_rules_when_a_glossary_alias_names_it(self):
        rules = [rule("Caroline Miller", "Malcolm", "cô–em")]
        chars = [Character("Caroline Miller", aliases=("Carolyn Miller",))]
        text = render_system(bundle(rules, cast=["Carolyn Miller", "Malcolm"], characters=chars))
        self.assertIn("Caroline Miller speaking to Malcolm", text)

    def test_without_a_cast_the_prompt_is_as_before(self):
        rules = [rule("Ned", "Ruth", "anh–chị")]
        self.assertIn("Ned speaking to Ruth", render_system(bundle(rules)))


class DropPairsTests(unittest.TestCase):
    BODY = "\n".join([
        "Inferred automatically.",
        "Characters so far:",
        "- Malcolm (male)",
        "- Lois",
        "Where each pair stands now:",
        "- Malcolm & Lois: family (mother and son), closeness 4/5, equals (as of S01E01)",
        "- Hal & Lois: romantic, closeness 5/5, equals (as of S01E01)",
    ])

    def test_a_pair_the_glossary_settles_is_not_printed_again(self):
        ruled = ruled_pairs([rule("Lois", "Malcolm", "mẹ–con")], None)
        out = drop_pairs(self.BODY, ruled)
        self.assertNotIn("Malcolm & Lois", out)
        self.assertIn("Hal & Lois", out)
        self.assertIn("- Malcolm (male)", out)  # characters stay

    def test_the_header_goes_with_the_last_pair(self):
        ruled = ruled_pairs([rule("Lois", "Malcolm", "x"), rule("Hal", "Lois", "y")], None)
        out = drop_pairs(self.BODY, ruled)
        self.assertNotIn("Where each pair stands now:", out)
        self.assertIn("Characters so far:", out)

    def test_nothing_left_but_the_hedge_means_no_section(self):
        body = "\n".join(["Inferred automatically.", "Where each pair stands now:", "- A & B: friend, closeness 1/5, equals (as of S01E01)"])
        self.assertEqual(drop_pairs(body, lambda a, b: True), "")

    def test_the_relationship_state_the_memory_builds_is_cleaned_in_the_prompt(self):
        facts = Facts.from_dict({
            "characters": [{"name": "Malcolm"}, {"name": "Lois"}, {"name": "Hal"}],
            "relationship_events": [
                {"a": "Malcolm", "b": "Lois", "relation": "family", "cue": 1, "intimacy": 4, "confidence": 0.9, "senior": "Lois"},
                {"a": "Hal", "b": "Lois", "relation": "romantic", "cue": 2, "intimacy": 5, "confidence": 0.9, "senior": "equal"},
            ],
            "terms": [], "summary": "s",
        })
        sections = build_context([UnitFacts("S01E01", facts)], None).sections
        rules = [rule("Malcolm", "Lois", "con–mẹ")]
        system = render_system(bundle(rules, cast=["Malcolm", "Lois", "Hal"], sections=sections))
        self.assertIn("Hal & Lois", system)
        self.assertNotIn("Malcolm & Lois", system)  # only the glossary rule speaks for the pair
        self.assertIn("Malcolm speaking to Lois", system)
        # a bundle with no rule keeps the section as it is
        plain = render_system(bundle([], sections=sections))
        self.assertIn("Malcolm & Lois", plain)

    def test_the_relationship_section_is_dropped_when_nothing_is_left(self):
        sections = [(RELATIONSHIP_TITLE, "Inferred.\nWhere each pair stands now:\n- Malcolm & Lois: family, closeness 4/5, equals (as of S01E01)")]
        system = render_system(bundle([rule("Malcolm", "Lois", "con–mẹ")], sections=sections))
        self.assertNotIn(RELATIONSHIP_TITLE, system)


class VoicesTests(unittest.TestCase):
    def test_a_voice_card_does_not_repeat_how_a_ruled_pair_addresses_each_other(self):
        card = CharacterCard("Malcolm", address=(("Lois", "con–mẹ"), ("Reese", "em–anh")))
        out = without_ruled_addresses([card], [rule("Lois", "Malcolm", "mẹ–con")], None)
        self.assertEqual(out[0].address, (("Reese", "em–anh"),))
        system = render_system(bundle([rule("Lois", "Malcolm", "mẹ–con")], voices=[card]))
        self.assertNotIn("Addresses Lois", system)
        self.assertIn('Addresses Reese: "em–anh"', system)

    def test_cards_are_returned_untouched_without_rules(self):
        card = CharacterCard("Malcolm", address=(("Lois", "con–mẹ"),))
        self.assertEqual(without_ruled_addresses([card], [], None), [card])


class BundleCastTests(unittest.TestCase):
    def test_cast_round_trips_and_is_left_out_when_unknown(self):
        known = bundle([], cast=["Lois", "Malcolm", "Lois"])
        self.assertEqual(known.cast, ("Lois", "Malcolm"))
        again = TranslationBundle.from_json(known.to_json())
        self.assertEqual(again.cast, known.cast)
        self.assertEqual(again.bundle_hash(), known.bundle_hash())
        self.assertNotIn("cast", bundle([]).to_dict())
        self.assertNotEqual(known.bundle_hash(), replace(known, cast=("Lois",)).bundle_hash())  # it changes the prompt

    def test_an_older_bundle_reads_as_one_with_an_unknown_cast(self):
        data = bundle([]).to_dict()
        data["bundle_version"] = 2
        self.assertEqual(TranslationBundle.from_dict(data).cast, ())
        self.assertEqual(BUNDLE_VERSION, 3)

    def test_the_prompt_version_moved(self):
        self.assertEqual(bundle([]).params.prompt_version, PROMPT_VERSION)
        self.assertEqual(PROMPT_VERSION, "p19-1")


class GlossaryAliasesTests(unittest.TestCase):
    def test_character_aliases_round_trip_through_yaml(self):
        g = Glossary(characters=(Character("Caroline Miller", target="Caroline Miller", aliases=("Carolyn Miller",)), Character("Lois")))
        text = dumps(g)
        self.assertIn("aliases", text)
        self.assertEqual(loads(text), g)
        self.assertNotIn("aliases", dumps(Glossary(characters=(Character("Lois"),))))  # no key, so old rule hashes hold

    def test_bad_aliases_are_refused(self):
        from subcore.glossary import GlossaryError

        with self.assertRaises(GlossaryError):
            loads("characters:\n  - name: Lois\n    aliases: [ok, '']\n")
        with self.assertRaises(GlossaryError):
            loads("characters:\n  - name: Lois\n    aliases: Lou\n")


if __name__ == "__main__":
    unittest.main()
