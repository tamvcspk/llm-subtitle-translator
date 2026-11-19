"""Review wave W3 (PLAN, "Code review P10-P14"): who a suggestion is (R13-6), how independent its sources are (R13-1),
what an accepted character does to a card the admin has (R13-3, R12-5) and how the generator spends and remembers its
calls (R13-4, R13-5). The source half (R13-2) is in ``packages/subcore/tests/test_review_w3.py``. Fakes only."""

import json
import time
import unittest
from unittest import mock

from subcore.providers import Unavailable

from subserver.proposals import SKIP_DAYS
from subserver.ports import SUGGESTION_REJECTED
from subserver.styles import Styles
from subserver.suggestion_keys import identity, identity_key
from subserver.suggestions import Proposal, independent_sources, source_host

from .test_proposals import Env as ProposerEnv
from .test_style_profile import Env as StyleEnv
from .test_suggestions import Env, character, rule, term


class SourceHostTests(unittest.TestCase):
    def test_a_site_is_one_place_whatever_its_scheme_prefix_path_or_language(self):
        same = [
            "https://fandom.com/wiki/Foo", "fandom.com/wiki/Bar", "http://www.fandom.com", "FANDOM.COM:443/x",
            "https://en.fandom.com/wiki/Foo", "//fandom.com",
        ]
        self.assertEqual({source_host(s) for s in same}, {"fandom.com"})
        self.assertEqual(source_host("https://en.wikipedia.org/wiki/X"), source_host("https://fr.wikipedia.org/wiki/X"))

    def test_a_two_part_suffix_keeps_the_site_name(self):
        self.assertEqual(source_host("https://www.bbc.co.uk/news"), "bbc.co.uk")
        self.assertEqual(source_host("https://a.b.example.com.au/x"), "example.com.au")
        self.assertNotEqual(source_host("https://bbc.co.uk"), source_host("https://itv.co.uk"))

    def test_what_is_not_an_address_is_its_own_place_and_nothing_is_none(self):
        self.assertEqual(source_host("Fandom wiki, page Foo"), "fandom wiki, page foo")
        self.assertEqual(source_host("  "), "")
        self.assertEqual(source_host(None), "")
        self.assertEqual(source_host("http://192.168.1.1/x"), "192.168.1.1")

    def test_pages_of_one_site_are_one_independent_source(self):
        self.assertEqual(independent_sources(["fandom.com/wiki/Foo", "fandom.com/wiki/Bar"]), 1)
        self.assertEqual(independent_sources(["https://en.wikipedia.org/a", "https://fr.wikipedia.org/a"]), 1)
        self.assertEqual(independent_sources(["fandom.com/wiki/Foo", "https://en.wikipedia.org/a", "", None, 3]), 2)
        self.assertEqual(independent_sources(None), 0)


class AutoAcceptSourceTests(unittest.TestCase):
    def test_a_term_with_two_pages_of_one_site_is_not_applied_by_itself(self):
        env = Env(self)  # auto_accept_terms is on by default
        pages = Proposal("term", {"source": "A", "target": "a"}, sources=("fandom.com/wiki/Foo", "fandom.com/wiki/Bar"))
        editions = Proposal(
            "term", {"source": "B", "target": "b"}, sources=("https://en.wikipedia.org/wiki/X", "https://fr.wikipedia.org/wiki/X"),
        )
        two_sites = Proposal("term", {"source": "C", "target": "c"}, sources=("fandom.com/wiki/Foo", "https://en.wikipedia.org/wiki/X"))
        report = env.propose(pages, editions, two_sites, source="research")
        self.assertEqual((report.added, report.auto_accepted), (3, 1))
        self.assertEqual([t.source for t in env.glossary().terms], ["C"])


class IdentityTests(unittest.TestCase):
    def test_what_is_decided_about_an_entry_holds_however_the_model_words_it_next_time(self):
        env = Env(self)
        env.propose(term(note="first wording"))
        env.suggestions.reject(env.pending()[0]["id"])
        again = env.propose(term(target="a different form", note="and a different note"))
        self.assertEqual((again.added, again.rejected_before), (0, 1))
        self.assertEqual(env.pending(), [])

    def test_an_accepted_entry_is_not_proposed_again_in_other_words(self):
        env = Env(self)
        env.propose(character(description="the crystal"))
        env.suggestions.accept(env.pending()[0]["id"])
        self.assertEqual(env.propose(character(target="Kyber!", description="other words")).accepted_before, 1)

    def test_a_pair_is_the_same_pair_whichever_side_is_named_first(self):
        env = Env(self)
        env.propose(rule(note="one"))
        flipped = env.propose(Proposal("address_rule", {"a": "Jinx", "b": "Kyber", "a_to_b": "em", "b_to_a": "anh", "note": "two"}))
        self.assertEqual((flipped.added, flipped.pending_already), (0, 1))
        later = env.propose(rule(**{"from": "S01E02"}))  # the same pair from another point is another entry
        self.assertEqual(later.added, 1)

    def test_the_identity_ignores_case_and_spacing_and_the_free_text(self):
        one = identity_key("term", {"source": "Kyber  Crystal", "target": "x", "note": "n"})
        two = identity_key("term", {"source": "kyber crystal", "target": "y"})
        self.assertEqual(one, two)
        self.assertEqual(identity("address_rule", {"a": "B", "b": "A"}), ("a", "b", ""))
        self.assertEqual(identity_key("term", {"target": "no source"}), "")  # a payload without one has no identity

    def test_a_rejection_made_before_the_identity_existed_still_holds_after_the_start_up_fills_it(self):
        """W0 lock: the migration adds the column empty and the start-up fills every row, decided ones included."""
        env = Env(self)
        env.propose(term())
        env.suggestions.reject(env.pending()[0]["id"] if env.pending() else 1)
        env.store._db.execute("UPDATE suggestions SET identity = ''")
        self.assertEqual(env.store.suggestions()[0].identity, "")
        env.store._fill_suggestion_identities()
        self.assertEqual(env.store.suggestions()[0].identity, identity_key("term", TERM_PAYLOAD))
        again = env.propose(term(target="reworded"))
        self.assertEqual((again.added, again.rejected_before), (0, 1))
        self.assertEqual(len(env.suggestions.listing(status=SUGGESTION_REJECTED)["items"]), 1)
        env.store._fill_suggestion_identities()  # idempotent
        self.assertEqual(len(env.store.suggestions()), 1)

    def test_the_store_itself_refuses_a_second_row_about_the_same_identity(self):
        env = Env(self)
        key = identity_key("term", TERM_PAYLOAD)
        args = (env.series, "vi", "term", "facts")
        self.assertIsNotNone(env.store.suggestion_add(*args, "fp1", TERM_PAYLOAD, [], [], identity=key))
        self.assertIsNone(env.store.suggestion_add(*args, "fp2", {**TERM_PAYLOAD, "target": "x"}, [], [], identity=key))
        self.assertEqual(len(env.store.suggestions()), 1)


TERM_PAYLOAD = {"source": "Kyber crystal", "target": "tinh thể Kyber"}


class CharacterMergeTests(unittest.TestCase):
    def test_accepting_a_voice_keeps_the_rest_of_a_card_the_admin_has_and_undo_puts_it_back_exactly(self):
        env = Env(self)
        mine = {"name": "Kyber", "aliases": ["K"], "catchphrases": ["Hum"], "speech": "slow", "always": True}
        env.style.save_card(env.series, {"card": mine})
        env.propose(character(card={"speech": "hums softly"}))
        item = env.pending()[0]
        env.suggestions.accept(item["id"])
        (card,) = env.styles.cards("series", "bazarr-7")
        self.assertEqual((card.speech, card.aliases, card.catchphrases, card.always), ("hums softly", ("K",), ("Hum",), True))
        env.suggestions.undo(item["id"])
        (back,) = env.styles.cards("series", "bazarr-7")
        self.assertEqual(back.to_doc(), {**mine, "aliases": ["K"]})

    def test_a_card_keeps_its_own_spelling_of_the_name(self):
        env = Env(self)
        env.style.save_card(env.series, {"card": {"name": "kyber", "speech": "slow"}})
        env.propose(character(card={"speech": "hums"}))
        env.suggestions.accept(env.pending()[0]["id"])
        self.assertEqual([c.name for c in env.styles.cards("series", "bazarr-7")], ["kyber"])

    def test_accepting_a_character_keeps_the_fields_of_an_entry_the_glossary_has(self):
        env = Env(self)
        env.glossaries.write("series", "bazarr-7", "vi", "characters:\n  - {name: Kyber, description: the crystal keeper}\n")
        env.propose(character())
        item = env.pending()[0]
        env.suggestions.accept(item["id"])
        (entry,) = env.glossary().characters
        self.assertEqual((entry.target, entry.gender, entry.description), ("Kyber", "f", "the crystal keeper"))
        env.suggestions.undo(item["id"])
        (back,) = env.glossary().characters
        self.assertEqual((back.target, back.gender, back.description), ("", "", "the crystal keeper"))

    def test_a_voice_that_says_what_the_card_already_says_changes_nothing(self):
        env = Env(self)
        env.style.save_card(env.series, {"card": {"name": "Kyber", "speech": "hums"}})
        env.glossaries.write("series", "bazarr-7", "vi", "characters:\n  - {name: Kyber, target: Kyber, gender: f}\n")
        report = env.propose(character(card={"speech": "hums"}))
        self.assertEqual((report.added, report.in_glossary), (0, 1))


class CardRenameTests(unittest.TestCase):
    def two_cards(self):
        env = StyleEnv(self)
        series = env.translated(1, 2)
        for name in ("Alice", "Bob"):
            env.style.save_card(series, {"card": {"name": name, "speech": name.lower()}})
        return env, series

    def test_renaming_a_card_onto_another_is_refused_with_its_name_and_nothing_changes(self):
        env, series = self.two_cards()
        for dry_run in (True, False):
            with self.assertRaises(ValueError) as caught:
                env.style.save_card(series, {"card": {"name": "bob", "speech": "x"}, "replaces": "Alice", "dry_run": dry_run})
            self.assertIn("bob", str(caught.exception).lower())
        self.assertEqual({c.name: c.speech for c in env.styles.cards("series", "bazarr-7")}, {"Alice": "alice", "Bob": "bob"})

    def test_the_store_refuses_it_too_and_leaves_both_cards(self):
        env, _ = self.two_cards()
        with self.assertRaises(ValueError):
            env.store.card_save("series", "bazarr-7", "BOB", {"name": "BOB"}, replaces="Alice")
        self.assertEqual(sorted(c.name for c in env.store.cards("series", "bazarr-7")), ["Alice", "Bob"])

    def test_the_glossary_half_of_a_character_is_not_written_when_its_card_cannot_be(self):
        env, series = self.two_cards()
        body = {
            "lang": "vi", "glossary": {"characters": [{"name": "Carol"}]},
            "card": {"card": {"name": "Bob"}, "replaces": "Alice"},
        }
        with self.assertRaises(ValueError):
            env.style.save_character(series, body)
        self.assertEqual(env.glossaries.load("series", "bazarr-7", "vi").characters, ())
        self.assertEqual(sorted(c.name for c in env.styles.cards("series", "bazarr-7")), ["Alice", "Bob"])

    def test_a_change_of_case_and_an_ordinary_rename_still_work(self):
        env, series = self.two_cards()
        env.style.save_card(series, {"card": {"name": "bob", "speech": "bob"}, "replaces": "Bob"})
        env.style.save_card(series, {"card": {"name": "Carol", "speech": "carol"}, "replaces": "Alice"})
        self.assertEqual(sorted(c.name for c in env.styles.cards("series", "bazarr-7")), ["Carol", "bob"])


class GeneratorTests(unittest.TestCase):
    def env(self):
        env = ProposerEnv(self, suggest_from_facts=False)
        env.translate(6)
        return env

    def test_a_provider_error_in_a_later_step_keeps_what_the_earlier_steps_were_paid_for(self):
        env = self.env()
        real = env.fake.handler

        def failing(req, call_no):
            if req.meta.get("what") == "characters":
                raise Unavailable("overloaded")
            return real(req, call_no)

        env.fake.handler = failing
        with self.assertRaises(ValueError) as caught:
            env.proposer.generate(env.series_id, "vi")
        self.assertIn("proposal(s) found before it are in the inbox", str(caught.exception))
        self.assertTrue(env.pending("address_rule") and env.pending("term"))
        self.assertEqual(env.pending("character"), [])

        env.fake.handler = real
        rules, terms = len(env.asked("rules")), len(env.asked("terms"))
        report = env.proposer.generate(env.series_id, "vi")  # safe to run again: it asks only about what is left
        self.assertEqual((len(env.asked("rules")), len(env.asked("terms"))), (rules, terms))
        self.assertEqual((report["address_rules"], report["terms"], report["characters"]), (0, 0, 2))
        self.assertEqual(sorted(i["payload"]["name"] for i in env.pending("character")), ["Alice", "Bob"])

    def test_the_typed_error_reaches_a_caller_that_maps_it(self):
        env = self.env()
        env.fake.handler = mock.Mock(side_effect=Unavailable("overloaded"))
        with self.assertRaises(Unavailable):
            env.proposer.run(env.series_id, "vi", lambda task: env.fake)

    def test_a_candidate_the_model_has_no_answer_for_is_remembered_for_a_while(self):
        env = self.env()
        real = env.fake.handler

        def silent(req, call_no):
            if req.meta.get("what") == "terms":
                items = [json.loads(line) for line in req.user.splitlines()[1:]]
                return {"terms": [{"i": x["i"], "target": "" if x["term"] == "Pair Term" else "VI " + x["term"]} for x in items]}
            return real(req, call_no)

        env.fake.handler = silent
        env.proposer.generate(env.series_id, "vi")
        self.assertEqual([i["payload"]["source"] for i in env.pending("term")], ["Kyber crystal"])
        asked = len(env.asked("terms"))
        again = env.proposer.generate(env.series_id, "vi")
        self.assertEqual((again["terms"], len(env.asked("terms"))), (0, asked))  # not asked about again ...
        later = time.time() + (SKIP_DAYS + 1) * 86400
        with mock.patch("subserver.proposals.time.time", return_value=later):
            self.assertEqual([t.source for t in env.proposer.scan(env.series_id, "vi").terms], ["Pair Term"])  # ... for a while

    def test_an_answer_that_could_not_be_used_at_all_is_not_remembered_as_unanswerable(self):
        env = self.env()
        real = env.fake.handler
        env.fake.handler = lambda req, n: "sorry" if req.meta.get("what") == "terms" else real(req, n)
        env.proposer.generate(env.series_id, "vi")
        self.assertEqual(len(env.proposer.scan(env.series_id, "vi").terms), 2)  # still to be asked

    def test_a_skip_belongs_to_the_prompt_it_was_made_under(self):
        env = self.env()
        store = env.suggestions.store
        store.suggestion_skip(env.series_id, "vi", [identity_key("term", {"source": "Pair Term"})], "s0")
        self.assertEqual(len(env.proposer.scan(env.series_id, "vi").terms), 2)  # another prompt: asked again

    def test_a_character_with_a_card_is_not_proposed_again(self):
        env = self.env()
        env.library.styles = Styles(env.store)
        env.library.styles.save_card("series", "bazarr-7", {"name": "Alice", "speech": "slow"})
        env.proposer.generate(env.series_id, "vi")
        self.assertEqual([i["payload"]["name"] for i in env.pending("character")], ["Bob"])
        self.assertNotIn('"name": "Alice"', env.asked("characters")[0].user)  # (Bob's relations still name her)


if __name__ == "__main__":
    unittest.main()
