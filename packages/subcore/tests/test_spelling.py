"""One spelling per character from the cast list (FR-5.10a, D-08): narrow on purpose, and never a guess between people."""

import unittest

from subcore.memory import Facts, UnitFacts, build_context, episode_cast, fingerprint, rename_facts, spelling_map


def facts(characters=(), events=(), summary="s"):
    return Facts.from_dict({
        "characters": [{"name": n} for n in characters], "relationship_events": list(events), "terms": [], "summary": summary,
    })


def event(a, b, cue=1):
    return {"a": a, "b": b, "relation": "friend", "cue": cue, "intimacy": 3, "confidence": 0.9}


class SpellingMapTests(unittest.TestCase):
    def test_one_close_cast_name_is_the_spelling(self):
        self.assertEqual(spelling_map(["Carolyn"], ["Caroline Miller", "Malcolm"]), {"Carolyn": "Caroline Miller"})
        self.assertEqual(spelling_map(["Carolyn Miller"], ["Caroline Miller"]), {"Carolyn Miller": "Caroline Miller"})

    def test_two_close_cast_names_merge_nothing(self):
        self.assertEqual(spelling_map(["Carolyn"], ["Caroline Miller", "Carolina Smith"]), {})

    def test_a_name_the_glossary_already_has_is_never_renamed(self):
        self.assertEqual(spelling_map(["Carolyn"], ["Caroline Miller"], keep=["Carolyn", "Lois"]), {})
        self.assertEqual(spelling_map(["Carolyn"], ["Caroline Miller"], keep=["carolyn"]), {})  # case does not matter

    def test_a_name_the_cast_has_or_abbreviates_is_left_alone(self):
        self.assertEqual(spelling_map(["Caroline Miller"], ["Caroline Miller"]), {})
        self.assertEqual(spelling_map(["Lois"], ["Lois Wilkerson", "Louis Smith"]), {})  # "Lois" is the first words of one

    def test_the_other_words_must_match_and_the_first_must_be_close(self):
        self.assertEqual(spelling_map(["Carolyn Jones"], ["Caroline Miller"]), {})
        self.assertEqual(spelling_map(["Ted"], ["Tad Mosby"]), {})  # too short to guess
        self.assertEqual(spelling_map(["Karolyn"], ["Caroline Miller"]), {})  # not the same first letter
        self.assertEqual(spelling_map(["Carol"], ["Caroline Miller"]), {})  # three edits away
        self.assertEqual(spelling_map(["Hal", "Reese"], ["Malcolm", "Lois"]), {})

    def test_no_cast_list_means_no_change(self):
        self.assertEqual(spelling_map(["Carolyn"], []), {})


class RenameFactsTests(unittest.TestCase):
    def test_names_aliases_and_events_take_the_cast_spelling(self):
        original = Facts.from_dict({
            "characters": [{"name": "Carolyn", "aliases": ["Miss M"]}, {"name": "Malcolm"}],
            "relationship_events": [event("Carolyn", "Malcolm")], "terms": [], "summary": "s",
        })
        renamed = rename_facts(original, {"Carolyn": "Caroline Miller"})
        self.assertEqual([c.name for c in renamed.characters], ["Caroline Miller", "Malcolm"])
        self.assertEqual((renamed.relationship_events[0].a, renamed.relationship_events[0].b), ("Caroline Miller", "Malcolm"))
        self.assertIs(rename_facts(original, {}), original)
        self.assertEqual(original.characters[0].name, "Carolyn")  # facts are frozen: the stored ones are not edited
        self.assertEqual(original.hash, Facts.from_dict(original.to_dict()).hash)


class ContextSpellingTests(unittest.TestCase):
    def prior(self):
        return [UnitFacts("S01E01", facts(["Carolyn", "Malcolm"], [event("Carolyn", "Malcolm")]))]

    def relationship(self, **kwargs):
        return dict(build_context(self.prior(), None, **kwargs).sections)["Relationship state"]

    def test_with_a_cast_the_story_state_uses_one_spelling(self):
        text = self.relationship(cast=["Caroline Miller", "Malcolm"])
        self.assertIn("Caroline Miller & Malcolm", text)
        self.assertNotIn("Carolyn", text)

    def test_without_a_cast_or_for_a_glossary_name_it_is_as_before(self):
        self.assertIn("Carolyn & Malcolm", self.relationship())
        self.assertIn("Carolyn & Malcolm", self.relationship(cast=["Caroline Miller"], keep=["Carolyn"]))

    def test_the_fingerprint_is_of_the_stored_facts_and_does_not_move(self):
        a = build_context(self.prior(), None)
        b = build_context(self.prior(), None, cast=["Caroline Miller"])
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.fingerprint, fingerprint(self.prior(), None))

    def test_the_episode_section_and_its_cast_use_the_list_spelling(self):
        own = facts(["Carolyn", "Malcolm"], [event("Carolyn", "Malcolm", cue=5)])
        context = build_context([], own, cast=["Caroline Miller"])
        self.assertIn("Caroline Miller & Malcolm", dict(context.sections)["This episode"])
        names = episode_cast([], own, main=["Caroline Miller"], guests=["Hal"])
        self.assertEqual(names, ["Caroline Miller", "Hal", "Malcolm"])
        self.assertEqual(episode_cast([], own, main=["Caroline Miller"], keep=["Carolyn"]), ["Caroline Miller", "Carolyn", "Malcolm"])
        self.assertEqual(episode_cast([], own), ["Carolyn", "Malcolm"])  # no outside list: as before


if __name__ == "__main__":
    unittest.main()
