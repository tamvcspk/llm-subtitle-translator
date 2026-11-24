"""P14: proposals drawn from story facts (SPEC FR-6, FR-18.3; criterion 21 a-b). The model is the fake provider."""

import json
import unittest

from subcore.glossary import AddressRule, Glossary, loads, merge
from subcore.memory import Character, Facts, FactTerm, RelationshipEvent
from subcore.providers import FakeProvider
from subcore.suggest import (
    CHANGE_MIN_EPISODES, Episode, SuggestError, address_drafts, address_items, character_drafts, character_items,
    is_group, term_drafts, term_items,
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
    def test_lovers_ep_1_to_3_then_enemies_ep_6_to_8_is_one_pair_item_with_two_stages(self):
        episodes = [
            episode(1, events=[event("Alice", "Bob", "romantic", 2)]),
            episode(2, events=[event("Alice", "Bob", "romantic", 3)]),
            episode(3, events=[event("Alice", "Bob", "romantic", 1)]),
            episode(6, events=[event("Alice", "Bob", "enemy", 4)]),
            episode(7, events=[event("Alice", "Bob", "enemy", 2)]),
            episode(8, events=[event("Alice", "Bob", "enemy", 5)]),
        ]
        (item,) = address_items(episodes, cues_of)
        self.assertEqual(CHANGE_MIN_EPISODES, 2)
        self.assertEqual((item.a.name, item.b.name, len(item.stages)), ("Alice", "Bob", 2))
        first, second = item.stages
        window = lambda st: (str(st.start or ""), str(st.until or ""))  # noqa: E731
        self.assertEqual((first.relation, *window(first)), ("romantic", "", "S01E06 00:00:06"))
        self.assertEqual((second.relation, *window(second)), ("enemy", "S01E06 00:00:06", ""))
        # the evidence carries the cue itself: number, timecode (from the source) and its text
        proof = second.evidence[0]
        self.assertEqual((proof["episode"], proof["cue"], proof["timecode"]), ("S01E06", 4, "00:00:06,000"))
        self.assertEqual(proof["text"], "S01E06 line 4")

    def test_siblings_oscillating_friend_rival_family_over_10_episodes_is_one_stage(self):
        labels = ["family", "rival", "friend", "family", "rival", "friend", "family", "rival", "friend", "family"]
        hiers = ["a_above", "equal", "a_above", "a_above", "b_above", "a_above", "a_above", "equal", "a_above", "a_above"]
        episodes = [
            episode(n, events=[event("Malcolm", "Reese", rel, 2, hierarchy=h, detail="brothers")])
            for n, (rel, h) in enumerate(zip(labels, hiers), start=1)
        ]
        (item,) = address_items(episodes, cues_of)
        self.assertEqual((item.relation, item.hierarchy, len(item.stages)), ("family", "a_above", 1))
        self.assertIsNone(item.stages[0].start)
        self.assertIsNone(item.stages[0].until)

    def test_one_deviant_episode_in_the_middle_does_not_open_a_stage(self):
        episodes = [
            episode(1, events=[event("Alice", "Bob", "friend", 1)]),
            episode(2, events=[event("Alice", "Bob", "friend", 1)]),
            episode(3, events=[event("Alice", "Bob", "rival", 1, confidence=0.9)]),
            episode(4, events=[event("Alice", "Bob", "friend", 1)]),
            episode(5, events=[event("Alice", "Bob", "friend", 1)]),
        ]
        (item,) = address_items(episodes, cues_of)
        self.assertEqual((item.relation, len(item.stages)), ("friend", 1))

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
        self.assertEqual(address_items(episodes, cues_of, glossary=held), [])  # no new change point -> admin's rule wins
        self.assertEqual(len(address_items(episodes, cues_of, glossary=Glossary())), 1)

    def test_existing_pair_gets_change_proposal_when_sturdy_change_appears(self):
        held = loads("address_rules:\n  - {a: Bob, b: Alice, a_to_b: anh–em, b_to_a: em–anh}\n")
        episodes = [
            episode(1, events=[event("Alice", "Bob", "romantic", 2)]),
            episode(2, events=[event("Alice", "Bob", "romantic", 2)]),
            episode(6, events=[event("Alice", "Bob", "enemy", 4)]),
            episode(7, events=[event("Alice", "Bob", "enemy", 2)]),
        ]
        (item,) = address_items(episodes, cues_of, glossary=held)
        self.assertEqual(str(item.change_from), "S01E06 00:00:06")
        self.assertEqual(len(item.stages), 2)
        self.assertEqual(len(item.existing), 1)

    def test_without_the_source_cues_there_is_still_a_proposal_but_no_timecode(self):
        episodes = [
            episode(1, events=[event("Alice", "Bob", "romantic", 2, quote="I love you")]),
            episode(2, events=[event("Alice", "Bob", "romantic", 2)]),
            episode(3, events=[event("Alice", "Bob", "enemy", 3)]),
            episode(4, events=[event("Alice", "Bob", "enemy", 3)]),
        ]
        (item,) = address_items(episodes, lambda label: [])
        self.assertEqual(str(item.stages[1].start), "S01E03")
        self.assertEqual((item.stages[0].evidence[0].get("timecode"), item.stages[0].evidence[0]["text"]), (None, "I love you"))

    def test_an_alias_met_later_still_folds_the_earlier_events_into_one_person(self):
        early = Episode("S01E01", 1, 1, Facts(
            (Character("Alice"), Character("Bob")), (event("Ali", "Bob", "friend", 1), event("Alice", "Bob", "friend", 2)), (), "",
        ))
        late = Episode("S01E02", 1, 2, Facts((Character("Alice", ("Ali",)), Character("Bob")), (), (), ""))
        (item,) = address_items([early, late], cues_of)  # "Ali" is Alice, not a third person
        self.assertEqual((item.a.name, len(item.evidence)), ("Alice", 1))  # 1 dominant reading per episode

    def test_glossary_with_address_rule_and_merge_treat_pair_order_symmetrically(self):
        g1 = loads("address_rules:\n  - {a: Alice, b: Bob, a_to_b: em–anh, b_to_a: anh–em}\n")
        updated = g1.with_address_rule(AddressRule("Bob", "Alice", a_to_b="tôi–cô", b_to_a="tôi–anh"))
        self.assertEqual(len(updated.address_rules), 1)
        self.assertEqual(updated.address_rules[0].a, "Bob")
        g2 = loads("address_rules:\n  - {a: Bob, b: Alice, a_to_b: tao–mày, b_to_a: tao–mày}\n")
        merged = merge(g1, g2)
        self.assertEqual(len(merged.address_rules), 1)
        self.assertEqual(merged.address_rules[0].a_to_b, "tao–mày")


class DoubtTests(unittest.TestCase):
    """What the first run on real episodes got wrong (PLAN P14, follow-up 5; updated for P18f)."""

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
        self.assertEqual((item.relation, len(item.stages), item.start, item.until), ("friend", 1, None, None))

    def test_an_explicit_high_confidence_turning_event_opens_a_stage(self):
        turning = [
            episode(1, events=[event("Alice", "Bob", "friend", 1)]),
            episode(2, events=[event("Alice", "Bob", "romantic", 3, confidence=0.9, detail="wedding ceremony")]),
        ]
        (item,) = address_items(turning, cues_of)
        self.assertEqual([st.relation for st in item.stages], ["friend", "romantic"])


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

    def test_multi_stage_pair_keeps_distinct_stages_and_collapses_identical_forms(self):
        items = address_items([
            episode(1, events=[event("Alice", "Bob", "romantic", 2)]),
            episode(2, events=[event("Alice", "Bob", "romantic", 2)]),
            episode(6, events=[event("Alice", "Bob", "enemy", 4)]),
            episode(7, events=[event("Alice", "Bob", "enemy", 2)]),
        ], cues_of)
        # Distinct forms across stages -> one draft with two stages and contiguous from/until.
        distinct = FakeProvider(handler=lambda req, n: {"rules": [{
            "i": 0, "a_to_b": "em–anh", "b_to_a": "anh–em", "why": "lovers",
            "stages": [{"stage": 1, "confirm": True, "a_to_b": "tôi–anh", "b_to_a": "tôi–cô", "why": "enemies"}],
        }]})
        (draft,) = address_drafts(distinct, items, lang="vi")
        self.assertEqual(len(draft.payload["stages"]), 2)
        self.assertEqual((draft.payload["stages"][0].get("from"), draft.payload["stages"][0].get("until")), (None, "S01E06 00:00:06"))
        self.assertEqual((draft.payload["stages"][1].get("from"), draft.payload["stages"][1].get("until")), ("S01E06 00:00:06", None))

        # Identical forms returned for both stages -> collapsed into one flat rule with no stages/from/until.
        same = FakeProvider(handler=lambda req, n: {"rules": [{
            "i": 0, "a_to_b": "tôi–cậu", "b_to_a": "tôi–cậu", "why": "same",
            "stages": [{"stage": 1, "confirm": True, "a_to_b": "tôi–cậu", "b_to_a": "tôi–cậu"}],
        }]})
        (collapsed,) = address_drafts(same, items, lang="vi")
        self.assertNotIn("stages", collapsed.payload)
        self.assertIsNone(collapsed.payload.get("from"))
        self.assertIsNone(collapsed.payload.get("until"))

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

    def test_r18_1_asymmetric_two_vs_three_stages_uses_stage_zero_as_base(self):
        # 2 episodes romantic then 3 episodes enemy -> overall majority is enemy,
        # but base (stage 0) sent to the model MUST be romantic, and candidate stage 1 enemy.
        eps = [
            episode(1, events=[event("Alice", "Bob", "romantic", 1, quote="lovers", detail="dating")]),
            episode(2, events=[event("Alice", "Bob", "romantic", 1, quote="lovers", detail="dating")]),
            episode(5, events=[event("Alice", "Bob", "enemy", 2, quote="betrayal", detail="rivals")]),
            episode(6, events=[event("Alice", "Bob", "enemy", 1, quote="hate", detail="rivals")]),
            episode(7, events=[event("Alice", "Bob", "enemy", 1, quote="hate", detail="rivals")]),
        ]
        (item,) = address_items(eps, cues_of)
        self.assertEqual(item.relation, "romantic")
        self.assertEqual([st.relation for st in item.stages], ["romantic", "enemy"])

    def test_r18_2_stray_family_episode_among_romantic_episodes_is_not_kinship(self):
        # 9 romantic episodes + 1 stray family reading (e.g. visiting in-laws) -> NOT kinship, base stays romantic.
        eps = [
            episode(n, events=[event(
                "Alice", "Bob", "family" if n == 4 else "romantic", 1, quote="line",
                detail="family dinner" if n == 4 else "husband and wife",
            )])
            for n in range(1, 11)
        ]
        (item,) = address_items(eps, cues_of)
        self.assertEqual(item.relation, "romantic")

        # And a genuine breakup later can still open a second stage.
        breakup = [
            *[episode(n, events=[event("Alice", "Bob", "family" if n == 2 else "romantic", 1, quote="love")]) for n in range(1, 6)],
            *[episode(n, events=[event("Alice", "Bob", "enemy", 1, quote="divorce")]) for n in range(6, 9)],
        ]
        (split,) = address_items(breakup, cues_of)
        self.assertEqual([st.relation for st in split.stages], ["romantic", "enemy"])

    def test_r18_6_existing_rule_without_timecode_covers_cue_level_change_in_same_episode(self):
        # Admin wrote `from: S01E05` (no timecode); facts give `S01E05 00:00:02` -> same episode, already covered.
        eps = [
            episode(1, events=[event("Alice", "Bob", "romantic", 1, quote="love")]),
            episode(2, events=[event("Alice", "Bob", "romantic", 1, quote="love")]),
            episode(5, events=[event("Alice", "Bob", "enemy", 2, quote="betray")]),
            episode(6, events=[event("Alice", "Bob", "enemy", 1, quote="hate")]),
        ]
        existing = loads(
            "version: 1\naddress_rules:\n"
            "  - {a: Alice, b: Bob, a_to_b: anh-em, b_to_a: em-anh, until: S01E05}\n"
            "  - {a: Alice, b: Bob, a_to_b: toi-co, b_to_a: toi-anh, from: S01E05}\n"
        )
        self.assertEqual(address_items(eps, cues_of, glossary=existing), [])

        # And exclude_changes matches by episode code even if timecode shifts.
        base_only = loads(
            "version: 1\naddress_rules:\n"
            "  - {a: Alice, b: Bob, a_to_b: anh-em, b_to_a: em-anh}\n"
        )
        self.assertEqual(
            address_items(eps, cues_of, glossary=base_only, exclude_changes={("alice", "bob", "s01e05")}),
            [],
        )
        self.assertEqual(
            address_items(eps, cues_of, glossary=base_only, exclude_changes={("alice", "bob", "")}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
