"""P14: proposals drawn from story facts (SPEC FR-6, FR-18.3, criterion 21 a-b). Every provider is a fake; nothing
touches the network. The facts are the canned ones of the story-memory tests (Alice and Bob, lovers in episode 1 and
enemies in episode 6, one shared term in every episode), plus a few terms of our own."""

import json
import unittest

from subcore.providers import Unavailable

from subserver import suggestion_api
from subserver.ports import SUGGESTION_PENDING, SUGGESTION_REJECTED
from subserver.proposals import FactsProposer
from subserver.suggestions import SuggestionService

from .test_story_memory import Env as MemoryEnv


class Env(MemoryEnv):
    def __init__(self, test, **settings):
        super().__init__(test)
        self.settings.update(settings)
        self.models = []
        self.suggestions = SuggestionService(self.store, self.library, settings_source=lambda: self.settings)
        self.proposer = FactsProposer(
            self.memory, self.suggestions, settings_source=lambda: self.settings,
            provider_factory=lambda settings, model, **kw: (self.models.append(model), self.fake)[1],
        )
        self.suggestions.generator = self.proposer
        self.attempt.proposals = self.proposer
        self.suggest_error = None
        for n in range(1, 7):
            self.add_source(n)

    # -- the fake model ------------------------------------------------------------------------------
    def handler(self, req, call_no):
        if req.meta.get("kind") == "suggest":
            if self.suggest_error is not None:
                raise self.suggest_error
            items = [json.loads(line) for line in req.user.splitlines()[1:]]
            what = req.meta["what"]
            if what == "rules":
                return {"rules": [{"i": x["i"], "a_to_b": "anh", "b_to_a": "em"} for x in items]}
            if what == "terms":
                return {"terms": [{"i": x["i"], "target": "VI " + x["term"], "note": "place"} for x in items]}
            return {"characters": [{"i": x["i"], "target": x["name"], "speech": "brisk"} for x in items]}
        reply = super().handler(req, call_no)
        if req.task == "extract":
            data = json.loads(reply)
            if req.meta["episode"] == 1:
                data["terms"] += [{"source": "Lone Term", "kind": "place"}, {"source": "Pair Term", "kind": "place"}]
            if req.meta["episode"] == 2:
                data["terms"].append({"source": "Pair Term", "kind": "place"})
            return json.dumps(data)
        return reply

    def asked(self, what=None):
        return [c for c in self.fake.calls if c.meta.get("kind") == "suggest" and what in (None, c.meta["what"])]

    # -- what the admin sees -----------------------------------------------------------------------------
    @property
    def series_id(self):
        return self.store.list_series()[0].id

    def pending(self, kind=None):
        items = self.suggestions.listing(status=SUGGESTION_PENDING)["items"]
        return [i for i in items if kind in (None, i["kind"])]

    def statuses(self):
        return {u["episode"]: u["status"] for u in self.library.series_units(self.series_id)["units"]}

    def glossary(self):
        return self.glossaries.load("series", "bazarr-7", "vi")


class AddressRuleTests(unittest.TestCase):
    def test_21a_a_pair_with_a_clear_relationship_gets_pending_rules_with_cue_and_timecode_and_nothing_is_applied(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        before = env.statuses()
        report = env.proposer.generate(env.series_id, "vi")

        first, second = sorted(env.pending("address_rule"), key=lambda i: i["payload"].get("from", ""))
        self.assertEqual((first["source"], first["status"], first["lang"]), ("facts", "pending", "vi"))
        self.assertEqual(first["payload"]["a"], "Alice")
        self.assertEqual((first["payload"]["a_to_b"], first["payload"]["b_to_a"]), ("anh", "em"))
        self.assertEqual(first["payload"]["until"], "S01E06 00:00:06")  # lovers until the turn ...
        self.assertEqual(second["payload"]["from"], "S01E06 00:00:06")  # ... enemies from it, to the second
        self.assertEqual(
            [(e["episode"], e["cue"], e["timecode"], e["text"]) for e in second["evidence"]],
            [("S01E06", 4, "00:00:06,000", "Line 4 of episode 6")],
        )
        self.assertEqual(second["evidence"][0]["unit_id"], env.unit_id(6))  # the page links to the episode
        self.assertEqual(first["evidence"][0]["episode"], "S01E01")
        self.assertEqual(first["evidence"][0]["timecode"], "00:00:02,000")  # cue 2 of episode 1
        self.assertEqual(report["added"], len(env.pending()))
        # not applied: the glossary is empty, no translation changed state, nothing was queued
        self.assertTrue(env.glossary().is_empty())
        self.assertEqual(env.statuses(), before)

    def test_a_pair_the_admin_already_has_a_rule_for_is_not_asked_about(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.glossaries.write("series", "bazarr-7", "vi", "address_rules:\n  - {a: Bob, b: Alice, a_to_b: em}\n")
        env.proposer.generate(env.series_id, "vi")
        self.assertEqual(env.asked("rules"), [])
        self.assertEqual(env.pending("address_rule"), [])

    def test_a_rule_the_admin_turned_down_is_not_asked_about_again_and_costs_no_call(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.proposer.generate(env.series_id, "vi")
        rules = env.pending("address_rule")
        for item in rules:
            env.suggestions.reject(item["id"])
        calls = len(env.asked())
        again = env.proposer.generate(env.series_id, "vi")
        self.assertEqual(len(env.asked()), calls)  # nothing new to ask: no call
        self.assertEqual((again["added"], again["calls"], env.pending("address_rule")), (0, 0, []))
        rejected = env.suggestions.listing(status=SUGGESTION_REJECTED)["items"]
        self.assertEqual(len([i for i in rejected if i["kind"] == "address_rule"]), len(rules))


class TermTests(unittest.TestCase):
    def test_21b_a_name_in_two_episodes_and_not_in_the_glossary_is_a_pending_term(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.proposer.generate(env.series_id, "vi")
        terms = {i["payload"]["source"]: i for i in env.pending("term")}
        self.assertEqual(sorted(terms), ["Kyber crystal", "Pair Term"])  # "Lone Term" is in one episode only
        kyber = terms["Kyber crystal"]
        self.assertEqual((kyber["source"], kyber["status"], kyber["payload"]["target"]), ("facts", "pending", "VI Kyber crystal"))
        self.assertEqual([e["episode"] for e in kyber["evidence"]], ["S01E01", "S01E02", "S01E03"])  # where it was found
        self.assertTrue(env.glossary().is_empty())
        self.assertEqual(env.suggestions.listing(status="accepted")["items"], [])
        self.assertNotIn("Lone Term", json.dumps(env.asked("terms")[0].user))

    def test_a_term_the_glossary_has_is_neither_asked_nor_proposed(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: kyber CRYSTAL, target: tinh thể Kyber}\n")
        env.proposer.generate(env.series_id, "vi")
        self.assertEqual([i["payload"]["source"] for i in env.pending("term")], ["Pair Term"])
        self.assertNotIn("Kyber crystal", env.asked("terms")[0].user)

    def test_a_proposal_from_facts_is_never_applied_by_itself_even_with_auto_accept_on(self):
        # auto_accept_terms is on by default but needs two independent research sources (FR-4.4); facts give none.
        env = Env(self, suggest_from_facts=False, auto_accept_terms=True)
        env.translate(6)
        report = env.proposer.generate(env.series_id, "vi")
        self.assertEqual(report["auto_accepted"], 0)
        self.assertTrue(env.pending("term"))
        self.assertTrue(env.glossary().is_empty())


class CharacterTests(unittest.TestCase):
    def test_a_character_of_two_episodes_is_proposed_from_memory_with_a_card_and_waits(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.proposer.generate(env.series_id, "vi")
        people = {i["payload"]["name"]: i for i in env.pending("character")}
        self.assertEqual(sorted(people), ["Alice", "Bob"])
        alice = people["Alice"]
        self.assertEqual((alice["source"], alice["status"]), ("memory", "pending"))
        self.assertEqual((alice["payload"]["gender"], alice["payload"]["card"]), ("f", {"name": "Alice", "speech": "brisk"}))
        self.assertTrue(alice["evidence"])  # the cues of the relationship it was read from
        self.assertEqual(env.glossary().characters, ())


class RunTests(unittest.TestCase):
    def test_a_translation_that_extracts_facts_leaves_proposals_in_the_inbox_and_costs_three_cheap_calls(self):
        env = Env(self)
        status = env.translate(6)
        self.assertEqual(status, "translated")
        self.assertTrue(env.pending("address_rule") and env.pending("term") and env.pending("character"))
        self.assertEqual([c.meta["what"] for c in env.asked()], ["rules", "terms", "characters"])
        self.assertTrue(all(c.task == "extract" for c in env.asked()))  # the cheap model's task (FR-9.3)
        self.assertEqual(env.fake.calls[-1].meta["what"], "characters")  # after the translation, not before it
        order = [c.task if c.meta.get("kind") != "suggest" else "suggest" for c in env.fake.calls]
        self.assertEqual(order, ["extract"] * 6 + ["translate"] + ["suggest"] * 3)

    def test_facts_that_were_already_stored_start_no_new_run(self):
        env = Env(self)
        env.translate(6)
        asked = len(env.asked())
        env.translate(5)  # its facts are cached: nothing was extracted
        self.assertEqual(len(env.asked()), asked)

    def test_a_generator_that_fails_never_fails_or_loses_the_job(self):
        env = Env(self)
        env.suggest_error = Unavailable("overloaded")
        self.assertEqual(env.translate(6), "translated")
        self.assertEqual(env.pending(), [])
        self.assertEqual(env.status(6), "current")  # the translation is published and in force

    def test_its_calls_carry_a_prompt_version_of_their_own_in_the_call_record(self):
        from subserver.translation_attempt import prompt_version_of

        self.assertEqual(prompt_version_of("extract", "suggest"), "suggest-s1")
        self.assertEqual(prompt_version_of("extract", "extract"), prompt_version_of("extract"))  # facts keep theirs

    def test_an_answer_that_is_not_json_costs_only_its_own_kind_of_proposal(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        real = env.handler

        def garbled(req, call_no):
            return "sorry" if req.meta.get("what") == "terms" else real(req, call_no)

        env.fake.handler = garbled
        report = env.proposer.generate(env.series_id, "vi")
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("terms", report["errors"][0])
        self.assertTrue(env.pending("address_rule") and env.pending("character"))
        self.assertEqual(env.pending("term"), [])

    def test_the_setting_that_turns_it_off_stops_the_hook_but_not_the_admins_own_request(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        self.assertEqual(env.asked(), [])
        self.assertEqual(env.proposer.generate(env.series_id, "vi")["ok"], True)
        self.assertTrue(env.asked())

    def test_the_cheap_model_runs_it_and_an_estimate_spends_nothing(self):
        env = Env(self, suggest_from_facts=False, memory_model="m-cheap")
        env.translate(6)
        calls = len(env.fake.calls)
        estimate = env.suggestions.generate(env.series_id, "vi", estimate_only=True)
        self.assertEqual(len(env.fake.calls), calls)
        self.assertEqual(
            (estimate["estimate_only"], estimate["episodes"], estimate["calls"], estimate["characters"]), (True, 6, 3, 2),
        )
        env.suggestions.generate(env.series_id, "vi")
        self.assertEqual(env.models, ["m-cheap"])

    def test_one_episode_gives_only_what_one_episode_can_show(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(1)  # one episode: its facts only; no term or character reaches two episodes
        report = env.proposer.generate(env.series_id, "vi")
        self.assertEqual((report["terms"], report["characters"], report["calls"]), (0, 0, 1))  # only the pair of episode 1

    def test_the_route_asks_for_a_series_and_a_language(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        ok = suggestion_api.handle(
            env.suggestions, "POST", "/api/suggestions/generate", {}, {"series_id": env.series_id, "lang": "vi", "estimate_only": True},
        )
        self.assertEqual(ok[0], 200)
        self.assertEqual(ok[1]["calls"], 3)
        self.assertEqual(suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/generate", {}, {"lang": "vi"})[0], 400)
        self.assertEqual(
            suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/generate", {}, {"series_id": env.series_id})[0], 400,
        )
        self.assertEqual(
            suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/generate", {}, {"series_id": 999, "lang": "vi"})[0], 404,
        )

    def test_no_generator_wired_is_a_clear_error(self):
        env = Env(self)
        env.suggestions.generator = None
        with self.assertRaises(ValueError):
            env.suggestions.generate(1, "vi")


if __name__ == "__main__":
    unittest.main()
