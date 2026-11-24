"""P14: proposals drawn from story facts (SPEC FR-6, FR-18.3, criterion 21 a-b). Every provider is a fake; nothing
touches the network. The facts are the canned ones of the story-memory tests (Alice and Bob, lovers in episode 1 and
enemies in episode 6, one shared term in every episode), plus a few terms of our own."""

import json
import unittest

from subcore.providers import Unavailable
from subcore.suggest import SUGGEST_PROMPT_VERSION

from subserver.api import suggestion_api
from subserver.domain.ports import SUGGESTION_PENDING, SUGGESTION_REJECTED
from subserver.services.proposals import FactsProposer
from subserver.jobs.read_jobs import SuggestJob
from subserver.services.suggestions import SuggestionService

from .test_story_memory import Env as MemoryEnv


class Env(MemoryEnv):
    def __init__(self, test, **settings):
        super().__init__(test)
        self.settings.update(settings)
        self.models = []
        self.suggestions = SuggestionService(self.store, self.library, settings_source=lambda: self.settings)
        self.proposer = FactsProposer(self.memory, self.suggestions, settings_source=lambda: self.settings)
        self.proposer.jobs = self.service.jobs  # a run is a job of the queue; run_jobs() is the worker
        self.suggestions.generator = self.proposer
        self.attempt.proposals = self.proposer
        self.handlers["suggest"] = SuggestJob(
            self.proposer, lambda settings, model, **kw: (self.models.append(model), self.fake)[1],
            settings_source=lambda: self.settings, usage=self.store,
        )
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

    def generate(self, lang="vi"):
        """The admin asks for proposals and the queue runs the job: the report the run left in the job."""
        job_id, _ = self.proposer.request(self.series_id, lang)
        self.run_jobs()
        return self.service.jobs.jobs.get(job_id)["report"]

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
    def test_21a_a_pair_with_a_clear_relationship_gets_one_pending_rule_with_cue_and_timecode_and_nothing_is_applied(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        before = env.statuses()
        report = env.generate()

        # P18f: one proposal per character pair (Alice & Bob have 1 ep romantic, 1 ep enemy -> 1 base stage).
        (pair,) = env.pending("address_rule")
        self.assertEqual((pair["source"], pair["status"], pair["lang"]), ("facts", "pending", "vi"))
        self.assertEqual((pair["payload"]["a"], pair["payload"]["b"]), ("Alice", "Bob"))
        self.assertEqual((pair["payload"]["a_to_b"], pair["payload"]["b_to_a"]), ("anh", "em"))
        self.assertEqual(pair["evidence"][0]["episode"], "S01E01")
        self.assertEqual(pair["evidence"][0]["timecode"], "00:00:02,000")
        self.assertEqual(pair["evidence"][0]["unit_id"], env.unit_id(1))
        self.assertEqual(report["added"], len(env.pending()))
        # not applied: the glossary is empty, no translation changed state, nothing was queued
        self.assertTrue(env.glossary().is_empty())
        self.assertEqual(env.statuses(), before)

    def test_p18f_multi_stage_pair_and_change_proposal_replace_and_undo_cleanly(self):
        env = Env(self, suggest_from_facts=False)
        # Seed sturdy 2+2 facts: ep 1-2 romantic, ep 5-6 enemy.
        for ep in (1, 2):
            env.add_source(ep)
        env.translate(6)
        # Write 2 romantic + 2 enemy episodes directly in facts so hysteresis (2 each side) triggers.
        from pathlib import Path
        from subcore.memory import Character, EXTRACT_PROMPT_VERSION, Facts, RelationshipEvent
        from subserver.services.versions import sha256_file
        for ep, rel, cue in ((1, "romantic", 2), (2, "romantic", 2), (5, "enemy", 3), (6, "enemy", 4)):
            uid = env.unit_id(ep)
            u = env.store.get_unit(uid)
            env.store.save_facts(
                uid, sha256_file(Path(u.source_path)), EXTRACT_PROMPT_VERSION, model="fake",
                facts=Facts(
                    (Character("Alice", gender="f"), Character("Bob", gender="m")),
                    (RelationshipEvent("Alice", "Bob", rel, cue, 3, "equal", f"ep {ep}", 0.9),),
                    (), f"ep {ep}",
                ),
            )
        real_handler = env.fake.handler

        def staged_handler(req, call_no):
            if req.meta.get("kind") == "suggest" and req.meta.get("what") == "rules":
                return {"rules": [{
                    "i": 0, "a_to_b": "em–anh", "b_to_a": "anh–em", "why": "lovers",
                    "stages": [{"stage": 1, "confirm": True, "a_to_b": "tôi–anh", "b_to_a": "tôi–cô", "why": "enemies"}],
                }]}
            return real_handler(req, call_no)

        env.fake.handler = staged_handler
        # Existing rule in reversed order (Bob, Alice): a sturdy change point generates a change proposal!
        env.glossaries.write("series", "bazarr-7", "vi", "address_rules:\n  - {a: Bob, b: Alice, a_to_b: anh–em, b_to_a: em–anh}\n")
        env.generate()
        (card,) = env.pending("address_rule")
        self.assertEqual(card["payload"]["change_from"], "S01E05 00:00:04")
        self.assertEqual(len(card["payload"]["stages"]), 2)
        # Accept replaces the old (Bob, Alice) rule with both new stages.
        env.suggestions.accept(card["id"])
        rules = env.glossary().address_rules
        self.assertEqual(len(rules), 2)
        self.assertEqual((rules[0].a_to_b, str(rules[0].until)), ("em–anh", "S01E05 00:00:04"))
        self.assertEqual((rules[1].a_to_b, str(rules[1].start)), ("tôi–anh", "S01E05 00:00:04"))
        # Undo restores the exact original (Bob, Alice) rule.
        env.suggestions.undo(card["id"])
        restored = env.glossary().address_rules
        self.assertEqual(len(restored), 1)
        self.assertEqual((restored[0].a, restored[0].b, restored[0].a_to_b), ("Bob", "Alice", "anh–em"))

    def test_p18f_legacy_pending_cards_of_same_pair_merge_into_one_when_regenerated(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        # Simulate two pre-P18f pending cards with legacy 3-part identities for Alice-Bob.
        env.store.suggestion_add(
            env.series_id, "vi", "address_rule", "facts", "fp-old-1",
            {"a": "Alice", "b": "Bob", "a_to_b": "em–anh", "from": "S01E01"}, [], [],
            identity=json.dumps(["address_rule", "alice", "bob", "s01e01"]),
        )
        env.store.suggestion_add(
            env.series_id, "vi", "address_rule", "facts", "fp-old-2",
            {"a": "Bob", "b": "Alice", "a_to_b": "tôi–cô", "from": "S01E03"}, [], [],
            identity=json.dumps(["address_rule", "alice", "bob", "s01e03"]),
        )
        self.assertEqual(len(env.pending("address_rule")), 2)
        env.generate()
        # Old per-stage pending cards were replaced by ONE pair card.
        (merged,) = env.pending("address_rule")
        self.assertEqual((merged["payload"]["a"], merged["payload"]["b"]), ("Alice", "Bob"))

    def test_a_pair_the_admin_already_has_a_rule_for_is_not_asked_about(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.glossaries.write("series", "bazarr-7", "vi", "address_rules:\n  - {a: Bob, b: Alice, a_to_b: em}\n")
        env.generate()
        self.assertEqual(env.asked("rules"), [])
        self.assertEqual(env.pending("address_rule"), [])

    def test_a_rule_the_admin_turned_down_is_not_asked_about_again_and_costs_no_call(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.generate()
        rules = env.pending("address_rule")
        for item in rules:
            env.suggestions.reject(item["id"])
        calls = len(env.asked())
        again = env.generate()
        self.assertEqual(len(env.asked()), calls)  # nothing new to ask: no call
        self.assertEqual((again["added"], again["calls"], env.pending("address_rule")), (0, 0, []))
        rejected = env.suggestions.listing(status=SUGGESTION_REJECTED)["items"]
        self.assertEqual(len([i for i in rejected if i["kind"] == "address_rule"]), len(rules))


class TermTests(unittest.TestCase):
    def test_21b_a_name_in_two_episodes_and_not_in_the_glossary_is_a_pending_term(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.generate()
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
        env.generate()
        self.assertEqual([i["payload"]["source"] for i in env.pending("term")], ["Pair Term"])
        self.assertNotIn("Kyber crystal", env.asked("terms")[0].user)

    def test_a_proposal_from_facts_is_never_applied_by_itself_even_with_auto_accept_on(self):
        # auto_accept_terms is on by default but needs two independent research sources (FR-4.4); facts give none.
        env = Env(self, suggest_from_facts=False, auto_accept_terms=True)
        env.translate(6)
        report = env.generate()
        self.assertEqual(report["auto_accepted"], 0)
        self.assertTrue(env.pending("term"))
        self.assertTrue(env.glossary().is_empty())


class CharacterTests(unittest.TestCase):
    def test_a_character_of_two_episodes_is_proposed_from_memory_with_a_card_and_waits(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        env.generate()
        people = {i["payload"]["name"]: i for i in env.pending("character")}
        self.assertEqual(sorted(people), ["Alice", "Bob"])
        alice = people["Alice"]
        self.assertEqual((alice["source"], alice["status"]), ("memory", "pending"))
        self.assertEqual((alice["payload"]["gender"], alice["payload"]["card"]), ("f", {"name": "Alice", "speech": "brisk"}))
        self.assertTrue(alice["evidence"])  # the cues of the relationship it was read from
        self.assertEqual(env.glossary().characters, ())


class RunTests(unittest.TestCase):
    def test_a_translation_that_extracts_facts_queues_a_look_for_proposals_and_spends_none_of_its_own_calls(self):
        env = Env(self)
        status = env.translate(6)
        self.assertEqual(status, "translated")
        self.assertEqual(env.asked(), [])  # the translation job made no call for it ...
        self.assertEqual(env.pending(), [])
        (job,) = env.service.jobs.jobs.by_state("queued")  # ... it only left a job for the queue to run on its own turn
        self.assertEqual((job["kind"], job["origin"], job["prio"]), ("suggest", "facts", 0))
        env.run_jobs()
        self.assertTrue(env.pending("address_rule") and env.pending("term") and env.pending("character"))
        self.assertEqual([c.meta["what"] for c in env.asked()], ["rules", "terms", "characters"])
        self.assertTrue(all(c.task == "extract" for c in env.asked()))  # the cheap model's task (FR-9.3)
        order = [c.task if c.meta.get("kind") != "suggest" else "suggest" for c in env.fake.calls]
        self.assertEqual(order, ["extract"] * 6 + ["translate"] + ["suggest"] * 3)  # after the translation, not before it

    def test_facts_that_were_already_stored_start_no_new_run(self):
        env = Env(self)
        env.translate(6)
        env.run_jobs()
        asked = len(env.asked())
        env.translate(5)  # its facts are cached: nothing was extracted
        self.assertEqual(env.service.jobs.jobs.open_count(), 0)
        env.run_jobs()
        self.assertEqual(len(env.asked()), asked)

    def test_a_series_whose_translations_all_read_facts_gets_one_open_look_not_one_per_translation(self):
        env = Env(self)
        env.translate(3)  # reads episodes 1 to 3
        env.translate(5)  # reads 4 and 5
        self.assertEqual([j["kind"] for j in env.service.jobs.jobs.by_state("queued")], ["suggest"])

    def test_the_look_the_admin_asks_for_is_interactive_and_lifts_the_one_a_translation_left(self):
        env = Env(self)
        env.translate(6)
        (left,) = env.service.jobs.jobs.by_state("queued")
        answer = env.suggestions.generate(env.series_id, "vi")
        self.assertEqual((answer["job_ids"], answer["queued"], answer["merged"]), ([left["job_id"]], 0, 1))
        self.assertEqual(env.service.jobs.jobs.get(left["job_id"])["prio"], 200)

    def test_a_generator_that_fails_never_fails_or_loses_the_job(self):
        env = Env(self)
        env.suggest_error = Unavailable("overloaded")
        self.assertEqual(env.translate(6), "translated")
        self.assertEqual(env.pending(), [])
        self.assertEqual(env.status(6), "current")  # the translation is published and in force
        env.run_jobs()  # the provider is down for the proposals: the job waits, nothing is lost
        (waiting,) = env.service.jobs.jobs.by_state("waiting", "queued")
        self.assertEqual(waiting["kind"], "suggest")

    def test_a_queue_that_cannot_take_the_job_never_reaches_the_translation(self):
        env = Env(self)
        env.proposer.jobs = None
        self.assertEqual(env.translate(6), "translated")  # nothing raised
        env.proposer.jobs = type("Broken", (), {"enqueue_suggest": lambda *a, **k: 1 / 0})()
        self.assertEqual(env.translate(5, trigger="retranslate", retranslate={}), "translated")

    def test_its_calls_carry_a_prompt_version_of_their_own_in_the_call_record(self):
        from subserver.jobs.provider_guard import prompt_version_of

        self.assertEqual(prompt_version_of("extract", "suggest"), f"suggest-{SUGGEST_PROMPT_VERSION}")
        self.assertEqual(prompt_version_of("extract", "extract"), prompt_version_of("extract"))  # facts keep theirs

    def test_an_answer_that_is_not_json_costs_only_its_own_kind_of_proposal(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        real = env.handler

        def garbled(req, call_no):
            return "sorry" if req.meta.get("what") == "terms" else real(req, call_no)

        env.fake.handler = garbled
        report = env.generate()
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("terms", report["errors"][0])
        self.assertTrue(env.pending("address_rule") and env.pending("character"))
        self.assertEqual(env.pending("term"), [])

    def test_the_setting_that_turns_it_off_stops_the_hook_but_not_the_admins_own_request(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(6)
        self.assertEqual((env.asked(), env.service.jobs.jobs.open_count()), ([], 0))  # the hook queued nothing
        self.assertEqual(env.generate()["ok"], True)
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
        self.assertEqual(len(env.fake.calls), calls)  # asking only queues the run
        env.run_jobs()
        self.assertEqual(env.models, ["m-cheap"])

    def test_one_episode_gives_only_what_one_episode_can_show(self):
        env = Env(self, suggest_from_facts=False)
        env.translate(1)  # one episode: its facts only; no term or character reaches two episodes
        report = env.generate()
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

    def test_r18_3_unconfirmed_change_proposal_is_skipped_and_second_run_makes_zero_calls(self):
        # Model rejects the candidate change (confirm: False -> collapsed to 1 stage -> no draft returned).
        def reject_change(req, n):
            if "rules" in str(req.response_schema):
                return {"rules": [{"i": 0, "a_to_b": "anh–em", "b_to_a": "em–anh", "stages": [{"stage": 1, "confirm": False}]}]}
            return _script(req, n)

        env = Env(self, suggest_from_facts=False, handler=reject_change)
        env.translate(6)  # episodes 1..6: romantic 1-3, enemy 4-6 -> change at S01E04
        # Pre-seed an existing glossary rule for Alice-Bob so the candidate is a `change_from` item.
        owner = env.library.series_owner(env.series_id)
        env.glossaries.save_doc(owner.kind, owner.key, "vi", {
            "version": 1,
            "address_rules": [{"a": "Alice", "b": "Bob", "a_to_b": "anh–em", "b_to_a": "em–anh"}],
        })
        first = env.generate()
        self.assertGreater(first["calls"], 0)
        # Second run over unchanged facts MUST make 0 calls (the unconfirmed change was remembered in suggestion_skips).
        second = env.generate()
        self.assertEqual(second["calls"], 0)
        self.assertEqual(second["address_rules"], 0)


if __name__ == "__main__":
    unittest.main()
