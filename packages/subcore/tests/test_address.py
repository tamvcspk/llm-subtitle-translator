"""Generic forms of address are not names, and the extraction prompt x4 (P19b, FR-5.9, D-02)."""

import json
import unittest

from subcore.memory import (
    EXTRACT_PROMPT_VERSION,
    FACTS_VERSIONS,
    Character,
    Facts,
    UnitFacts,
    build_context,
    clean_facts,
    extract_facts,
    fold,
    is_generic_address,
    merge_declared,
    render_episode,
)
from subcore.providers import FakeProvider
from subcore.suggest import Episode, address_items, character_items

from .helpers import numbered


def event(a, b, relation="family", cue=1, **extra):
    return {"a": a, "b": b, "relation": relation, "cue": cue, "intimacy": 4, "confidence": 0.9, "evidence": "", **extra}


def facts(characters=(), events=(), summary=""):
    return Facts.from_dict({"characters": list(characters), "relationship_events": list(events), "terms": [], "summary": summary})


class GenericAddressTests(unittest.TestCase):
    def test_forms_of_address_are_recognised_and_proper_names_are_not(self):
        for name in ("Mom", "mom", "Mother", "Dad.", "Daddy", "Sir", "Ma'am", "Ma’am", "Honey", "my son", "the Coach", "Doc"):
            self.assertTrue(is_generic_address(name), name)
        for name in ("Lois", "Malcolm", "Doc Brown", "Mother Teresa", "Sir Lancelot", "Coach Carter"):
            self.assertFalse(is_generic_address(name), name)

    def test_an_unknown_language_falls_back_to_english(self):
        self.assertTrue(is_generic_address("Mom", "fr"))
        self.assertTrue(is_generic_address("Mom", "en-US"))


class CleanFactsTests(unittest.TestCase):
    def test_a_character_named_mom_with_a_proper_alias_becomes_that_person(self):
        clean = clean_facts(facts([{"name": "Mom", "aliases": ["Lois"], "gender": "f"}, {"name": "Lois"}]))
        self.assertEqual([(c.name, c.aliases) for c in clean.characters], [("Lois", ())])
        self.assertEqual(clean.characters[0].gender, "f")

    def test_a_generic_alias_is_not_an_alias(self):
        clean = clean_facts(facts([{"name": "Lois", "aliases": ["Mom", "Mrs. Wilkerson"]}]))
        self.assertEqual(clean.characters[0].aliases, ("Mrs. Wilkerson",))

    def test_a_character_known_only_by_a_form_of_address_is_dropped(self):
        clean = clean_facts(facts([{"name": "Mom"}, {"name": "Sir", "aliases": ["Dad"]}, {"name": "Hal"}]))
        self.assertEqual([c.name for c in clean.characters], ["Hal"])

    def test_two_people_called_mom_do_not_merge(self):
        """Lois's "Mom" is Ida: `Ida (aka Mom)` and `Lois (aka Mom)` stay two people, never one Lois."""
        clean = clean_facts(facts([{"name": "Lois", "aliases": ["Mom"]}, {"name": "Ida", "aliases": ["Mom"]}]))
        self.assertEqual(sorted(c.name for c in clean.characters), ["Ida", "Lois"])
        folded = fold([UnitFacts("S01E01", facts([{"name": "Lois", "aliases": ["Mom"]}, {"name": "Ida", "aliases": ["Mom"]}]))])
        self.assertEqual(sorted(c.name for c in folded.characters), ["Ida", "Lois"])

    def test_an_event_end_is_named_by_the_one_proper_name_the_unit_ties_to_the_form(self):
        clean = clean_facts(facts(
            [{"name": "Mom", "aliases": ["Lois"]}, {"name": "Malcolm"}], [event("Malcolm", "Mom", hierarchy="b_above")],
        ))
        (e,) = clean.relationship_events
        self.assertEqual((e.a, e.b, e.hierarchy), ("Malcolm", "Lois", "b_above"))

    def test_an_event_with_an_unresolvable_form_of_address_is_dropped(self):
        for characters in ([{"name": "Malcolm"}], [{"name": "Lois", "aliases": ["Mom"]}, {"name": "Ida", "aliases": ["Mom"]}]):
            clean = clean_facts(facts(characters, [event("Malcolm", "Mom")]))
            self.assertEqual(clean.relationship_events, (), characters)

    def test_an_event_that_collapses_onto_one_person_is_dropped(self):
        clean = clean_facts(facts([{"name": "Mom", "aliases": ["Lois"]}], [event("Lois", "Mom")]))
        self.assertEqual(clean.relationship_events, ())

    def test_clean_facts_return_the_same_object_when_nothing_changes_and_never_edit_the_source(self):
        plain = facts([{"name": "Lois"}], [event("Lois", "Hal")])
        self.assertIs(clean_facts(plain), plain)
        dirty = facts([{"name": "Mom", "aliases": ["Lois"]}])
        before = dirty.hash
        clean_facts(dirty)
        self.assertEqual(dirty.hash, before)


class MergeDeclaredTests(unittest.TestCase):
    def test_an_entry_that_declares_another_as_alias_merges_in_one_unit(self):
        merged = merge_declared([Character("Carolyn Miller", ("Caroline Miller",)), Character("Caroline Miller")])
        self.assertEqual([c.name for c in merged], ["Caroline Miller"])  # the longer of the two
        self.assertEqual(merged[0].aliases, ("Carolyn Miller",))

    def test_the_longer_proper_name_wins(self):
        merged = merge_declared([Character("Lois", ("Lois Wilkerson",)), Character("Lois Wilkerson")])
        self.assertEqual((merged[0].name, merged[0].aliases), ("Lois Wilkerson", ("Lois",)))

    def test_a_shared_nickname_alone_keeps_two_people(self):
        merged = merge_declared([Character("Malcolm", ("Mal",)), Character("Malik", ("Mal",))])
        self.assertEqual([c.name for c in merged], ["Malcolm", "Malik"])


class FoldWithAddressTests(unittest.TestCase):
    def test_mom_aka_lois_and_lois_are_one_lois_and_no_form_of_address_is_a_character(self):
        unit = UnitFacts("S02E04", facts(
            [{"name": "Mom", "aliases": ["Lois"]}, {"name": "Lois"}, {"name": "Ida", "aliases": ["Mom"]}, {"name": "Malcolm"}],
            [event("Malcolm", "Mom", cue=2), event("Lois", "Ida", cue=3, detail="mother and daughter")],
        ))
        state = fold([unit])
        self.assertEqual(sorted(c.name for c in state.characters), ["Ida", "Lois", "Malcolm"])
        self.assertEqual({tuple(sorted((r.a, r.b))) for r in state.relationships}, {("Ida", "Lois")})
        context = build_context([unit], None)
        text = "\n".join(body for _, body in context.sections)
        self.assertNotIn("Mom", text)

    def test_the_fingerprint_does_not_move_when_facts_are_read_cleaned(self):
        dirty = UnitFacts("S01E01", facts([{"name": "Mom", "aliases": ["Lois"]}]))
        self.assertEqual(build_context([dirty], None).fingerprint, build_context([dirty], None).fingerprint)
        from subcore.memory import fingerprint
        self.assertEqual(build_context([dirty], None).fingerprint, fingerprint([dirty], None))

    def test_this_episode_section_names_people_not_forms_of_address(self):
        own = facts([{"name": "Mom", "aliases": ["Lois"]}], [event("Malcolm", "Mom"), event("Malcolm", "Dad")])
        section = render_episode(own)
        self.assertIn("Malcolm & Lois", section[1])
        self.assertNotIn("Mom", section[1])
        self.assertNotIn("Dad", section[1])


class SuggestionsWithAddressTests(unittest.TestCase):
    def episodes(self, count=3):
        return [
            Episode(f"S01E0{n}", 1, n, facts(
                [{"name": "Mom", "aliases": ["Lois"]}, {"name": "Lois"}, {"name": "Malcolm"}],
                [event("Malcolm", "Mom", detail="mother and son"), event("Malcolm", "Sir", relation="professional")],
            ))
            for n in range(1, count + 1)
        ]

    def test_address_proposals_have_no_form_of_address_for_a_person(self):
        items = address_items(self.episodes(), lambda label: ())
        pairs = {tuple(sorted((i.a.name, i.b.name))) for i in items}
        self.assertEqual(pairs, {("Lois", "Malcolm")})

    def test_character_proposals_never_offer_mom(self):
        names = {item.character.name for item in character_items(self.episodes(), lambda label: ())}
        self.assertNotIn("Mom", names)
        self.assertNotIn("Sir", names)
        self.assertIn("Lois", names)


class ExtractPromptX4Tests(unittest.TestCase):
    def reply(self, **kw):
        return json.dumps({"characters": [], "relationship_events": [], "terms": [], "summary": "ok", **kw})

    def test_the_version_is_x4_and_x3_facts_are_still_accepted(self):
        self.assertEqual(EXTRACT_PROMPT_VERSION, "x4")
        self.assertEqual(FACTS_VERSIONS, ("x4", "x3"))

    def test_the_prompt_asks_for_proper_names_and_senior(self):
        provider = FakeProvider(handler=lambda req, n: self.reply())
        extract_facts(provider, numbered(2, "line {n}"), title="Show")
        (request,) = provider.calls
        self.assertIn("never a form of address", request.system)
        self.assertIn("senior", request.system)
        self.assertNotIn("a_above", request.system)
        props = request.response_schema["properties"]["relationship_events"]["items"]["properties"]
        self.assertIn("senior", props)
        self.assertNotIn("hierarchy", props)

    def test_senior_names_the_senior_person(self):
        parsed = facts(events=[
            event("Lois", "Malcolm", senior="Lois"),
            event("Lois", "Malcolm", senior="malcolm", cue=2),
            event("Lois", "Malcolm", senior="equal", cue=3),
            event("Lois", "Malcolm", senior="Someone Else", cue=4),
        ])
        self.assertEqual([e.hierarchy for e in parsed.relationship_events], ["a_above", "b_above", "equal", "equal"])

    def test_old_x3_facts_with_hierarchy_still_parse_and_keep_their_hash(self):
        old = {"characters": [{"name": "Lois", "aliases": [], "gender": "f", "age_hint": "", "role": ""}],
               "relationship_events": [{"a": "Malcolm", "b": "Lois", "relation": "family", "intimacy": 4, "hierarchy": "b_above",
                                        "cue": 1, "evidence": "", "confidence": 0.9, "detail": ""}],
               "terms": [], "summary": ""}
        parsed = Facts.from_dict(old)
        self.assertEqual(parsed.relationship_events[0].hierarchy, "b_above")
        self.assertEqual(parsed.to_dict(), old)
        self.assertEqual(Facts.from_dict(parsed.to_dict()).hash, parsed.hash)


if __name__ == "__main__":
    unittest.main()
