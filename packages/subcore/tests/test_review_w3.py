"""Review wave W3 (PLAN, "Code review P10-P14"), the source half: a candidate is read only as far as the story had shown
it (R13-2), a character the admin already has a card for is not proposed again (R13-3), and what must not be asked about
leaves the list before its limit applies (R13-5). The model is not involved."""

import unittest

from subcore.memory import Character, Facts, FactTerm
from subcore.suggest import Episode, address_items, character_items, term_items

from .helpers import make_cues
from .test_suggest import episode, event


def cues_of(label):
    return make_cues([f"{label} line {n}" for n in range(1, 11)])


def facts(characters, *, events=(), terms=()):
    return Facts(tuple(characters), tuple(events), tuple(FactTerm(t, "place") for t in terms), "")


class CharacterReadingTests(unittest.TestCase):
    def episodes(self):
        return [
            Episode("S01E01", 1, 1, facts(
                [Character("Alice", gender="f", role="a clerk"), Character("Bob", gender="m")],
                events=[event("Alice", "Bob", "friend", 1)],
            )),
            Episode("S01E02", 1, 2, facts([Character("Alice", gender="f", age_hint="young"), Character("Bob", gender="m")])),
            Episode("S01E03", 1, 3, facts(
                [Character("Alice", gender="f", role="the traitor", age_hint="old"), Character("Bob", gender="m")],
                events=[event("Alice", "Bob", "enemy", 1)],
            )),
        ]

    def test_a_character_is_read_only_up_to_the_episode_where_it_qualified(self):
        """A card applies to every episode, so what it says must not come from an episode after the second one Alice is
        in (a unit never sees the facts of a later unit)."""
        alice = next(i for i in character_items(self.episodes(), cues_of) if i.character.name == "Alice")
        self.assertEqual(alice.units, ("S01E01", "S01E02"))
        self.assertEqual(alice.character.role, "a clerk")  # not "the traitor" of episode 3 ...
        self.assertEqual(alice.character.age_hint, "young")  # ... nor the age it shows there
        self.assertEqual(alice.relations, ("friend with Bob",))  # the enemy of episode 3 is not in it
        self.assertEqual({e["episode"] for e in alice.evidence}, {"S01E01"})

    def test_a_character_that_qualifies_late_reads_everything_up_to_then(self):
        episodes = [episode(1, characters=("Alice",)), episode(2, characters=("Bob",)), episode(3), episode(4)]
        bob = next(i for i in character_items(episodes, cues_of) if i.character.name == "Bob")
        self.assertEqual(bob.units, ("S01E02", "S01E03"))

    def test_the_ranking_still_counts_every_episode(self):
        episodes = [episode(1, characters=("Alice", "Bob")), episode(2, characters=("Alice", "Bob")),
                    episode(3, characters=("Bob",)), episode(4, characters=("Bob",))]
        self.assertEqual([i.character.name for i in character_items(episodes, cues_of)], ["Bob", "Alice"])

    def test_a_character_with_a_card_is_not_proposed_and_neither_is_one_known_by_an_alias_of_it(self):
        episodes = [episode(1), episode(2)]
        self.assertEqual([i.character.name for i in character_items(episodes, cues_of, cards=["alice"])], ["Bob"])
        self.assertEqual([i.character.name for i in character_items(episodes, cues_of, cards=["Bob", "Alice"])], [])
        with_alias = [
            Episode("S01E01", 1, 1, facts([Character("Alice", ("Ali",))])),
            Episode("S01E02", 1, 2, facts([Character("Alice", ("Ali",))])),
        ]
        self.assertEqual(character_items(with_alias, cues_of, cards=["ali"]), [])


class ExcludeBeforeTheLimitTests(unittest.TestCase):
    def test_what_is_excluded_does_not_crowd_the_rest_out_of_the_limit(self):
        names = [f"Term{n:02d}" for n in range(35)]
        episodes = [episode(1, terms=names), episode(2, terms=names)]
        first = term_items(episodes, cues_of, limit=30)
        self.assertEqual(len(first), 30)
        held = {item.key for item in first}
        rest = term_items(episodes, cues_of, limit=30, exclude=held)
        self.assertEqual([i.source for i in rest], names[30:])  # not an empty list

    def test_an_address_state_and_a_character_can_be_excluded_by_key(self):
        episodes = [episode(1, events=[event("Alice", "Bob", "friend", 1)]), episode(2)]
        (item,) = address_items(episodes, cues_of)
        self.assertEqual(address_items(episodes, cues_of, exclude={item.key}), [])
        self.assertEqual([i.character.name for i in character_items(episodes, cues_of, exclude={("alice",)})], ["Bob"])

    def test_the_keys_are_blind_to_case_and_spacing(self):
        episodes = [episode(1, terms=["Kyber  Crystal"]), episode(2, terms=["Kyber  Crystal"])]
        (item,) = term_items(episodes, cues_of)
        self.assertEqual(item.key, ("kyber crystal", ""))
        self.assertEqual(term_items(episodes, cues_of, exclude={("kyber crystal", "")}), [])


if __name__ == "__main__":
    unittest.main()
