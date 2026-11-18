"""P14: proposals drawn from story facts (SPEC FR-6, FR-18.3; criterion 21 a-b). The model is the fake provider."""

import json
import unittest

from subcore.glossary import Glossary, loads
from subcore.memory import Character, Facts, FactTerm, RelationshipEvent
from subcore.providers import FakeProvider
from subcore.suggest import (
    Episode, SuggestError, address_drafts, address_items, character_drafts, character_items, is_group, term_drafts,
    term_items,
)

from .helpers import make_cues


def episode(n, *, characters=("Alice", "Bob"), events=(), terms=()):
    facts = Facts(
        tuple(Character(c, gender="f" if c == "Alice" else "m", role="the lead" if c == "Alice" else "") for c in characters),
        tuple(events), tuple(FactTerm(t, "place") for t in terms), f"summary {n}",
    )
    return Episode(f"S01E{n:02d}", 1, n, facts)


def event(a, b, relation, cue, *, hierarchy="equal", confidence=0.9, quote="a line", detail=""):
    return RelationshipEvent(a, b, relation, cue, 3, hierarchy, quote, confidence, detail)


def cues_of(label):
    return make_cues([f"{label} line {n}" for n in range(1, 11)])  # cue n starts at (n - 1) * 2 s


class AddressItemTests(unittest.TestCase):
    def test_a_pair_that_turns_from_lovers_to_enemies_has_two_states_with_the_timecode_of_the_turn(self):
        episodes = [
            episode(1, events=[event("Alice", "Bob", "romantic", 2)]),
            episode(2),
            episode(6, events=[event("Alice", "Bob", "enemy", 4)]),
        ]
        first, second = address_items(episodes, cues_of)
        window = lambda item: (str(item.start or ""), str(item.until or ""))  # noqa: E731
        self.assertEqual((first.relation, *window(first)), ("romantic", "", "S01E06 00:00:06"))
        self.assertEqual((second.relation, *window(second)), ("enemy", "S01E06 00:00:06", ""))
        # the evidence is the cue itself: number, timecode (from the source) and its text
        (proof,) = second.evidence
        self.assertEqual((proof["episode"], proof["cue"], proof["timecode"]), ("S01E06", 4, "00:00:06,000"))
        self.assertEqual(proof["text"], "S01E06 line 4")

    def test_an_unchanged_state_is_one_item_and_a_repeated_event_adds_evidence(self):
        episodes = [
            episode(1, events=[event("Alice", "Bob", "friend", 1)]),
            episode(2, events=[event("Bob", "Alice", "friend", 5)]),  # the other way round: still the same pair and state
        ]
        (item,) = address_items(episodes, cues_of)
        self.assertEqual([e["episode"] for e in item.evidence], ["S01E01", "S01E02"])
        self.assertEqual((item.a.name, item.b.name), ("Alice", "Bob"))  # always in name order

    def test_the_hierarchy_follows_the_pair_when_it_is_turned_round(self):
        (item,) = address_items([episode(1, events=[event("Bob", "Alice", "professional", 1, hierarchy="a_above")])], cues_of)
        self.assertEqual((item.a.name, item.hierarchy), ("Alice", "b_above"))  # Bob (a in the facts) is above Alice

    def test_doubtful_readings_strangers_and_pairs_the_glossary_already_rules_are_left_out(self):
        episodes = [episode(1, events=[
            event("Alice", "Bob", "friend", 1, confidence=0.3), event("Alice", "Zed", "stranger", 2),
        ])]
        self.assertEqual(address_items(episodes, cues_of), [])
        held = loads("address_rules:\n  - {a: Bob, b: Alice, a_to_b: em}\n")
        episodes = [episode(1, events=[event("Alice", "Bob", "friend", 1)])]
        self.assertEqual(address_items(episodes, cues_of, glossary=held), [])  # the admin's rule wins, either way round
        self.assertEqual(len(address_items(episodes, cues_of, glossary=Glossary())), 1)

    def test_without_the_source_cues_there_is_still_a_proposal_but_no_timecode(self):
        episodes = [
            episode(1, events=[event("Alice", "Bob", "romantic", 2, quote="I love you")]),
            episode(2, events=[event("Alice", "Bob", "enemy", 3)]),
        ]
        first, second = address_items(episodes, lambda label: [])
        self.assertEqual(str(second.start), "S01E02")
        self.assertEqual((first.evidence[0].get("timecode"), first.evidence[0]["text"]), (None, "I love you"))  # no key, not ""

    def test_an_alias_met_later_still_folds_the_earlier_events_into_one_person(self):
        early = Episode("S01E01", 1, 1, Facts(
            (Character("Alice"), Character("Bob")), (event("Ali", "Bob", "friend", 1), event("Alice", "Bob", "friend", 2)), (), "",
        ))
        late = Episode("S01E02", 1, 2, Facts((Character("Alice", ("Ali",)), Character("Bob")), (), (), ""))
        (item,) = address_items([early, late], cues_of)  # "Ali" is Alice, not a third person
        self.assertEqual((item.a.name, len(item.evidence)), ("Alice", 2))


class DoubtTests(unittest.TestCase):
    """What the first run on real episodes got wrong (PLAN P14, follow-up 5)."""

    def test_a_name_that_lists_several_people_is_not_a_person(self):
        group = "Malcolm, Reese and Dewey"
        episodes = [
            episode(1, characters=("Alice", group), events=[event("Alice", group, "family", 1), event("Alice", "Bob", "friend", 2)]),
            episode(2, characters=("Alice", group)),
        ]
        (item,) = address_items(episodes, cues_of)
        self.assertEqual((item.a.name, item.b.name), ("Alice", "Bob"))  # the pair with the group is left out, not split
        self.assertEqual([i.character.name for i in character_items(episodes, cues_of)], ["Alice"])
        for name in ("Hal and Lois", "Reese & Dewey", "Lois/Hal", "Hal và Lois"):
            self.assertTrue(is_group(name), name)
        for name in ("Andy", "Randall", "Lois", "Dr. Smith"):
            self.assertFalse(is_group(name), name)

    def test_one_doubtful_reading_is_not_a_new_stage(self):
        flip = [
            episode(1, events=[event("Alice", "Bob", "friend", 1)]),
            episode(2, events=[event("Alice", "Bob", "rival", 1, confidence=0.55)]),
            episode(3, events=[event("Alice", "Bob", "friend", 1)]),
        ]
        (item,) = address_items(flip, cues_of)  # friends, a misread, friends again: one stage
        self.assertEqual((item.relation, item.start, item.until, len(item.evidence)), ("friend", None, None, 2))

    def test_a_confident_or_repeated_change_is_still_a_stage(self):
        confident = [episode(1, events=[event("Alice", "Bob", "friend", 1)]), episode(2, events=[event("Alice", "Bob", "rival", 1, confidence=0.9)])]
        self.assertEqual([i.relation for i in address_items(confident, cues_of)], ["friend", "rival"])
        repeated = [
            episode(1, events=[event("Alice", "Bob", "friend", 1)]),
            episode(2, events=[event("Alice", "Bob", "rival", 1, confidence=0.55), event("Alice", "Bob", "rival", 5, confidence=0.55)]),
        ]
        self.assertEqual([i.relation for i in address_items(repeated, cues_of)], ["friend", "rival"])


class TermAndCharacterItemTests(unittest.TestCase):
    def test_a_term_needs_two_episodes_and_no_glossary_entry_and_is_not_a_character(self):
        episodes = [
            episode(1, terms=["Kyber", "Lone", "Alice"]), episode(2, terms=["kyber", "Mars", "Alice"]),
            episode(3, terms=["Mars", "Kyber"]),
        ]
        items = term_items(episodes, cues_of)
        self.assertEqual([(i.source, len(i.units)) for i in items], [("Kyber", 3), ("Mars", 2)])  # most widespread first
        have = loads("terms:\n  - {source: Kyber, target: x}\n")
        self.assertEqual([i.source for i in term_items(episodes, cues_of, glossary=have)], ["Mars"])
        alias = loads("terms:\n  - {source: Red Planet, target: x, aliases: [Mars]}\n")
        self.assertEqual([i.source for i in term_items(episodes, cues_of, glossary=alias)], ["Kyber"])

    def test_the_evidence_of_a_term_is_the_first_cue_that_has_it(self):
        def named(label):
            return make_cues(["nothing", "the Kyber glows", "more"])

        (item,) = term_items([episode(1, terms=["Kyber"]), episode(2, terms=["Kyber"])], named)
        self.assertEqual([(e["episode"], e["cue"], e["timecode"]) for e in item.evidence], [("S01E01", 2, "00:00:02,000"), ("S01E02", 2, "00:00:02,000")])

    def test_a_character_needs_two_episodes_and_no_glossary_entry(self):
        episodes = [
            episode(1, characters=("Alice", "Bob"), events=[event("Alice", "Bob", "enemy", 1, quote="you")]),
            episode(2, characters=("Alice", "Zed")),
        ]
        (item,) = character_items(episodes, cues_of)
        self.assertEqual((item.character.name, len(item.units), item.relations), ("Alice", 2, ("enemy with Bob",)))
        have = loads("characters:\n  - {name: alice, target: Alice}\n")
        self.assertEqual(character_items(episodes, cues_of, glossary=have), [])


class FormsTests(unittest.TestCase):
    def item(self):
        return address_items([episode(1, events=[event("Alice", "Bob", "romantic", 2, detail="husband and wife")])], cues_of)

    def test_the_forms_become_a_rule_with_its_note_and_the_prompt_has_no_timecode_or_index(self):
        provider = FakeProvider(handler=lambda req, n: json.dumps({"rules": [{"i": 0, "a_to_b": "em–anh", "b_to_a": "anh–em"}]}))
        (draft,) = address_drafts(provider, self.item(), lang="vi")
        self.assertEqual(draft.kind, "address_rule")
        self.assertEqual(draft.payload, {"a": "Alice", "b": "Bob", "a_to_b": "em–anh", "b_to_a": "anh–em", "note": "romantic: husband and wife"})
        self.assertEqual(draft.evidence[0]["timecode"], "00:00:02,000")  # shown to the admin ...
        (call,) = provider.calls
        self.assertNotIn("00:00:02", call.user)  # ... never sent to the model
        self.assertNotIn("-->", call.user)
        self.assertEqual((call.task, call.meta["kind"]), ("extract", "suggest"))
        self.assertIn("`vi`", call.system)

    def test_the_reason_the_model_gives_goes_into_the_note_for_the_reviewer(self):
        provider = FakeProvider(handler=lambda req, n: {"rules": [{"i": 0, "a_to_b": "em–anh", "why": "Alice is his wife"}]})
        (draft,) = address_drafts(provider, self.item(), lang="vi")
        self.assertEqual(draft.payload["note"], "romantic: husband and wife. Alice is his wife")
        (call,) = provider.calls
        self.assertIn("why", call.system)

    def test_a_state_after_the_first_carries_its_window(self):
        items = address_items([
            episode(1, events=[event("Alice", "Bob", "romantic", 2)]), episode(2, events=[event("Alice", "Bob", "enemy", 3)]),
        ], cues_of)
        provider = FakeProvider(handler=lambda req, n: {"rules": [{"i": i, "a_to_b": "x", "b_to_a": ""} for i in range(2)]})
        first, second = address_drafts(provider, items, lang="vi")
        self.assertEqual((first.payload.get("from"), first.payload.get("until")), (None, "S01E02 00:00:04"))
        self.assertEqual((second.payload.get("from"), second.payload.get("until")), ("S01E02 00:00:04", None))

    def test_empty_forms_unknown_items_and_junk_entries_are_dropped(self):
        answer = {"rules": [{"i": 0, "a_to_b": "", "b_to_a": " "}, {"i": 7, "a_to_b": "x"}, "junk", {"a_to_b": "x"}]}
        provider = FakeProvider(handler=lambda req, n: json.dumps(answer))
        self.assertEqual(address_drafts(provider, self.item(), lang="vi"), [])

    def test_an_answer_that_is_not_json_is_a_suggest_error_after_two_tries(self):
        provider = FakeProvider(handler=lambda req, n: "I cannot do that")
        with self.assertRaises(SuggestError):
            address_drafts(provider, self.item(), lang="vi")
        self.assertEqual(len(provider.calls), 2)

    def test_nothing_to_ask_is_no_call(self):
        provider = FakeProvider()
        self.assertEqual(address_drafts(provider, [], lang="vi"), [])
        self.assertEqual(term_drafts(provider, [], lang="vi"), [])
        self.assertEqual(character_drafts(provider, [], lang="vi"), [])
        self.assertEqual(provider.calls, [])

    def test_a_term_keeps_its_source_and_takes_the_target_the_model_gave(self):
        items = term_items([episode(1, terms=["Kyber"]), episode(2, terms=["Kyber"])], cues_of)
        provider = FakeProvider(handler=lambda req, n: {"terms": [{"i": 0, "target": "Kyber", "note": "crystal"}]})
        (draft,) = term_drafts(provider, items, lang="vi", title="Show")
        self.assertEqual((draft.kind, draft.payload), ("term", {"source": "Kyber", "target": "Kyber", "note": "crystal"}))
        self.assertEqual(len(draft.evidence), 2)

    def test_a_character_card_only_when_the_model_says_how_they_speak(self):
        items = character_items([episode(1), episode(2)], cues_of)
        speaks = {"characters": [{"i": 0, "target": "Alice", "speech": "clipped"}, {"i": 1, "target": "", "speech": ""}]}
        provider = FakeProvider(handler=lambda req, n: speaks)
        alice, bob = character_drafts(provider, items, lang="vi")
        self.assertEqual(alice.payload, {"name": "Alice", "target": "Alice", "gender": "f", "description": "the lead", "card": {"speech": "clipped"}})
        self.assertEqual(bob.payload, {"name": "Bob", "gender": "m"})  # nothing invented


if __name__ == "__main__":
    unittest.main()
