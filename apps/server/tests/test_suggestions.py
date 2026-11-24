"""P13: the suggestions table and the Review inbox's use case (SPEC FR-4.4, FR-11.1, FR-11.1.5, criterion 21 c-h).
The generators are fakes (they only hand over a list of proposals); nothing touches the network."""

import unittest

from subcore import glossary as g

from subserver.api import suggestion_api
from subserver.domain.ports import SUGGESTION_ACCEPTED, SUGGESTION_PENDING, SUGGESTION_REJECTED
from subserver.services.suggestions import SOURCES, Proposal, SuggestionService, fingerprint, independent_sources, normalise

from .test_style_profile import Env as StyleEnv

TERM = {"source": "Kyber crystal", "target": "tinh thể Kyber"}
RULE = {"a": "Kyber", "b": "Jinx", "a_to_b": "anh", "b_to_a": "em"}
EVIDENCE = [{"unit": 1, "cue": 5, "timecode": "00:00:08,000", "text": "The Kyber crystal glows"}]


class Env(StyleEnv):
    def __init__(self, test, **settings):
        super().__init__(test)
        self.settings = dict(settings)
        self.suggestions = SuggestionService(self.store, self.library, settings_source=lambda: self.settings)
        self.series = self.translated(1, 2)

    def propose(self, *proposals, source="facts", lang="vi"):
        return self.suggestions.propose(self.series, lang, list(proposals), source=source)

    def pending(self, **query):
        return self.suggestions.listing(status=SUGGESTION_PENDING, **query)["items"]

    def glossary(self, lang="vi"):
        return self.glossaries.load("series", "bazarr-7", lang)


def term(**extra):
    return Proposal("term", {**TERM, **extra}, evidence=tuple(EVIDENCE))


def rule(**extra):
    return Proposal("address_rule", {**RULE, **extra}, evidence=tuple(EVIDENCE))


def character(**extra):
    return Proposal("character", {"name": "Kyber", "target": "Kyber", "gender": "f", **extra})


class ProposeTests(unittest.TestCase):
    def test_21a_a_proposal_waits_with_its_evidence_and_changes_no_translation(self):
        env = Env(self)
        before = env.statuses()
        report = env.propose(rule())
        self.assertEqual((report.added, report.auto_accepted), (1, 0))
        (item,) = env.pending()
        self.assertEqual((item["kind"], item["source"], item["status"], item["lang"]), ("address_rule", "facts", "pending", "vi"))
        self.assertEqual(item["evidence"], EVIDENCE)  # the cue and its timecode a person can check
        self.assertEqual(item["payload"], {"a": "Kyber", "b": "Jinx", "a_to_b": "anh", "b_to_a": "em"})
        self.assertTrue(env.glossary().is_empty())  # not applied
        self.assertEqual(env.statuses(), before)  # no translation is touched, no job is made
        self.assertEqual(env.queued(), [])

    def test_the_same_content_is_one_row_whatever_its_case_and_spacing(self):
        env = Env(self)
        env.propose(term())
        again = env.propose(term(source="  kyber   CRYSTAL ", target="Tinh Thể Kyber"))
        self.assertEqual((again.added, again.pending_already), (0, 1))
        self.assertEqual(len(env.pending()), 1)

    def test_a_different_wording_of_the_same_source_is_the_same_suggestion(self):
        """R13-6: what a series decided holds for the entry, however the model words it next time."""
        env = Env(self)
        env.propose(term())
        again = env.propose(term(target="đá Kyber"))
        self.assertEqual((again.added, again.pending_already), (0, 1))

    def test_what_the_glossary_already_says_is_not_proposed(self):
        env = Env(self)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber}\n")
        report = env.propose(term())
        self.assertEqual((report.added, report.in_glossary), (0, 1))
        self.assertEqual(env.pending(), [])

    def test_a_bad_proposal_is_reported_and_the_others_still_go_in(self):
        env = Env(self)
        report = env.propose(
            Proposal("term", {"source": "x"}), Proposal("nonsense", {}), Proposal("address_rule", {"a": "A", "b": "B"}), term(),
        )
        self.assertEqual((report.added, len(report.invalid)), (1, 3))
        self.assertIn("proposal 0", report.invalid[0])
        self.assertIn("terms[0]", report.invalid[0])  # the glossary's own message names the path

    def test_an_unknown_source_language_or_series_is_refused(self):
        env = Env(self)
        with self.assertRaises(ValueError):
            env.propose(term(), source="rumour")
        with self.assertRaises(ValueError):
            env.propose(term(), lang="../x")
        with self.assertRaises(LookupError):
            env.suggestions.propose(999, "vi", [term()], source="facts")

    def test_the_proposals_of_two_languages_are_kept_apart(self):
        env = Env(self)
        env.propose(term(), lang="vi")
        self.assertEqual(env.propose(term(), lang="fr").added, 1)
        self.assertEqual(len(env.pending(lang="fr")), 1)


class AcceptTests(unittest.TestCase):
    def test_21c_accept_writes_the_glossary_in_the_database_and_flags_translations_but_redoes_none(self):
        env = Env(self)
        env.propose(term())
        (item,) = env.pending()
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        answer = env.suggestions.accept(item["id"])
        self.assertEqual((answer["saved"], answer["would_stale"]), (True, 2))
        stored = env.glossaries.stored("series", "bazarr-7", "vi")
        self.assertEqual(stored.doc["terms"], [TERM])  # in the database, in the YAML schema
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})  # flagged ...
        self.assertEqual(env.queued(), [])  # ... never redone (FR-5.6)
        self.assertEqual(env.suggestions.listing(status=SUGGESTION_ACCEPTED)["items"][0]["status"], "accepted")
        self.assertEqual(env.pending(), [])

    def test_the_dry_run_says_the_same_number_and_writes_nothing(self):
        env = Env(self)
        env.propose(term())
        (item,) = env.pending()
        asked = env.suggestions.accept(item["id"], dry_run=True)
        self.assertEqual((asked["saved"], asked["would_stale"]), (False, 2))
        self.assertTrue(env.glossary().is_empty())
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        self.assertEqual(len(env.pending()), 1)
        self.assertEqual(env.suggestions.accept(item["id"])["would_stale"], asked["would_stale"])

    def test_a_suggestion_the_glossary_already_has_flags_nothing(self):
        env = Env(self)
        env.propose(term())
        (item,) = env.pending()
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber}\n")
        answer = env.suggestions.accept(item["id"], dry_run=True)
        self.assertEqual(answer["unchanged"], True)

    def test_a_decided_suggestion_cannot_be_decided_again(self):
        env = Env(self)
        env.propose(term(), rule())
        first, second = env.pending()
        env.suggestions.accept(first["id"])
        env.suggestions.reject(second["id"])
        for call in (env.suggestions.accept, env.suggestions.reject):
            for item in (first, second):
                with self.assertRaises(ValueError):
                    call(item["id"])
        with self.assertRaises(LookupError):
            env.suggestions.accept(999)

    def test_21d_edit_then_accept_writes_the_edited_value(self):
        env = Env(self)
        env.propose(term())
        (item,) = env.pending()
        env.suggestions.accept(item["id"], payload={**TERM, "target": "đá Kyber", "locked": True})
        self.assertEqual(env.glossary().terms[0].target, "đá Kyber")
        self.assertTrue(env.glossary().terms[0].locked)
        (done,) = env.suggestions.listing(status=SUGGESTION_ACCEPTED)["items"]
        self.assertEqual((done["payload"]["target"], done["edited"]), ("đá Kyber", True))

    def test_an_edit_that_does_not_read_is_refused_and_nothing_is_written(self):
        env = Env(self)
        env.propose(rule())
        (item,) = env.pending()
        with self.assertRaises(ValueError) as caught:
            env.suggestions.accept(item["id"], payload={"a": "Kyber", "b": "Jinx"})  # no a_to_b nor b_to_a
        self.assertIn("address_rules[0]", str(caught.exception))
        self.assertTrue(env.glossary().is_empty())
        self.assertEqual(len(env.pending()), 1)

    def test_21d_a_rejected_suggestion_is_not_proposed_again(self):
        env = Env(self)
        env.propose(rule())
        (item,) = env.pending()
        env.suggestions.reject(item["id"])
        self.assertEqual(env.glossary().address_rules, ())
        again = env.propose(rule(), source="memory")  # another generator, the same content
        self.assertEqual((again.added, again.rejected_before), (0, 1))
        self.assertEqual(env.pending(), [])
        self.assertEqual(env.suggestions.listing(status=SUGGESTION_REJECTED)["counts"]["rejected"], 1)

    def test_an_accepted_suggestion_is_skipped_by_the_next_run(self):
        env = Env(self)
        env.propose(term())
        env.suggestions.accept(env.pending()[0]["id"])
        again = env.propose(term())
        self.assertEqual((again.added, again.accepted_before), (0, 1))

    def test_a_term_replaces_the_one_with_the_same_source_and_start(self):
        env = Env(self)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: old}\n  - {source: Jinx, target: Jinx}\n")
        env.propose(term())
        env.suggestions.accept(env.pending()[0]["id"])
        self.assertEqual([(t.source, t.target) for t in env.glossary().terms], [("Jinx", "Jinx"), ("Kyber crystal", "tinh thể Kyber")])


class BulkTests(unittest.TestCase):
    def two_series(self):
        env = Env(self)
        other = env.store.upsert_series("bazarr-8", title="Other", bazarr_id="8")
        env.suggestions.propose(other, "vi", [term(source="Zed", target="Zed")], source="facts")
        env.propose(term(), rule(), character())
        return env, other

    def test_21e_accept_all_touches_only_that_series(self):
        env, other = self.two_series()
        done = env.suggestions.bulk(env.series, expect=3)
        self.assertEqual((done["count"], done["failed"]), (3, []))
        glossary = env.glossary()
        self.assertEqual((len(glossary.terms), len(glossary.address_rules), len(glossary.characters)), (1, 1, 1))
        self.assertTrue(env.glossaries.load("series", "bazarr-8", "vi").is_empty())  # the other series is untouched
        left = env.pending()
        self.assertEqual([(i["series_id"], i["payload"].get("source")) for i in left], [(other, "Zed")])

    def test_the_count_the_admin_confirmed_must_still_be_right(self):
        env, _ = self.two_series()
        with self.assertRaises(ValueError) as caught:
            env.suggestions.bulk(env.series, expect=2)
        self.assertIn("list changed", str(caught.exception))
        with self.assertRaises(ValueError):
            env.suggestions.bulk(env.series)  # a bulk accept without the confirmed number is refused
        self.assertTrue(env.glossary().is_empty())

    def test_the_dry_run_counts_what_all_of_them_together_would_flag(self):
        env, _ = self.two_series()
        asked = env.suggestions.bulk(env.series, dry_run=True)
        self.assertEqual((asked["count"], asked["saved"], asked["would_stale"]), (3, False, 2))
        self.assertTrue(env.glossary().is_empty())
        self.assertEqual(len(env.pending()), 4)

    def test_one_that_fails_does_not_stop_the_rest(self):
        env, _ = self.two_series()
        (broken,) = [i for i in env.pending(series_id=env.series) if i["kind"] == "character"]
        env.store.suggestion_decide(broken["id"], SUGGESTION_PENDING, payload={"name": ""})  # a hand-edited row
        done = env.suggestions.bulk(env.series, expect=3)
        self.assertEqual((done["count"], len(done["failed"])), (2, 1))
        self.assertEqual(done["failed"][0]["id"], broken["id"])

    def test_bulk_accept_folds_writes_and_counts_impact_once_with_per_item_undo(self):
        env = Env(self)
        env.propose(
            term(source="Kyber crystal", target="tinh thể Kyber"),
            term(source="Hextech", target="Hextech"),
            rule(),
            character(name="Vi", card={"speech": "direct"}),
            character(name="Jinx", card={"speech": "playful"}),
        )
        impact_calls = 0
        real_impact = env.library.impact_units

        def count_impact(*args, **kwargs):
            nonlocal impact_calls
            impact_calls += 1
            return real_impact(*args, **kwargs)

        env.library.impact_units = count_impact
        done = env.suggestions.bulk(env.series, expect=5)
        self.assertEqual((done["count"], done["failed"], done["would_stale"]), (5, [], 2))
        self.assertEqual(impact_calls, 1)  # counted once for the whole language batch, not 5 times
        # Glossary was written once (version 1, no intermediate history revisions).
        stored = env.glossaries.stored("series", "bazarr-7", "vi")
        self.assertEqual(stored.version, 1)
        self.assertEqual(env.glossaries.history("series", "bazarr-7", "vi"), [])
        self.assertEqual({c.name for c in env.styles.cards("series", "bazarr-7")}, {"Vi", "Jinx"})
        # Each suggestion still has its own undo record and can be undone individually.
        accepted_items = env.suggestions.listing(status=SUGGESTION_ACCEPTED)["items"]
        self.assertEqual(len(accepted_items), 5)
        hextech = next(i for i in accepted_items if i["payload"].get("source") == "Hextech")
        env.suggestions.undo(hextech["id"])
        self.assertEqual([t.source for t in env.glossary().terms], ["Kyber crystal"])
        vi = next(i for i in accepted_items if i["payload"].get("name") == "Vi")
        env.suggestions.undo(vi["id"])
        self.assertEqual([c.name for c in env.styles.cards("series", "bazarr-7")], ["Jinx"])


class AutoAcceptTests(unittest.TestCase):
    def test_21f_a_kind_with_auto_accept_on_is_applied_shown_in_the_history_and_can_be_undone(self):
        env = Env(self, auto_accept_address_rules=True)
        report = env.propose(rule(), character())
        self.assertEqual((report.added, report.auto_accepted), (2, 1))
        self.assertEqual(len(env.suggestions.listing(status=SUGGESTION_PENDING)["items"]), 1)  # the other kind waits
        self.assertEqual(env.pending()[0]["kind"], "character")
        self.assertEqual(len(env.glossary().address_rules), 1)  # applied
        (auto,) = env.suggestions.listing(status=SUGGESTION_ACCEPTED)["items"]  # in the history, marked
        self.assertEqual((auto["kind"], auto["auto_accepted"], auto["undoable"]), ("address_rule", True, True))
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})

        undone = env.suggestions.undo(auto["id"])
        self.assertTrue(undone["saved"])
        self.assertEqual(env.glossary().address_rules, ())
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        self.assertEqual({i["kind"] for i in env.pending()}, {"address_rule", "character"})  # back in the inbox
        self.assertFalse([i for i in env.pending() if i["auto_accepted"]])

    def test_the_defaults_apply_nothing_by_themselves(self):
        env = Env(self)
        report = env.propose(term(), rule(), character())
        self.assertEqual((report.added, report.auto_accepted), (3, 0))
        self.assertTrue(env.glossary().is_empty())

    def test_a_term_is_applied_by_itself_only_with_two_independent_sources(self):
        env = Env(self)  # auto_accept_terms is on by default
        one = Proposal("term", {"source": "A", "target": "a"}, sources=("https://wiki.example/a", "https://www.wiki.example/b"))
        two = Proposal("term", {"source": "B", "target": "b"}, sources=("https://wiki.example/a", "https://fandom.example/b"))
        none = Proposal("term", {"source": "C", "target": "c"})
        report = env.propose(one, two, none, source="research")
        self.assertEqual((report.added, report.auto_accepted), (3, 1))  # the same host twice is one source
        self.assertEqual([t.source for t in env.glossary().terms], ["B"])
        self.assertEqual({i["payload"]["source"] for i in env.pending()}, {"A", "C"})

    def test_the_switch_for_terms_can_be_turned_off(self):
        env = Env(self, auto_accept_terms=False)
        two = Proposal("term", {"source": "B", "target": "b"}, sources=("https://x.example/1", "https://y.example/2"))
        self.assertEqual(env.propose(two, source="research").auto_accepted, 0)

    def test_an_auto_accept_that_cannot_be_applied_stays_pending(self):
        env = Env(self, auto_accept_address_rules=True)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: x, target: y}\n")
        original = env.suggestions._commit
        env.suggestions._commit = lambda *a, **k: (_ for _ in ()).throw(ValueError("the glossary does not read"))
        report = env.propose(rule())
        env.suggestions._commit = original
        self.assertEqual((report.added, report.auto_accepted, len(report.failed_to_apply)), (1, 0, 1))
        self.assertEqual(len(env.pending()), 1)


class UndoTests(unittest.TestCase):
    def test_undo_puts_back_the_entry_the_accept_replaced(self):
        env = Env(self)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: old, note: mine}\n")
        env.propose(term())
        env.suggestions.accept(env.pending()[0]["id"])
        self.assertEqual(env.glossary().terms[0].target, "tinh thể Kyber")
        env.suggestions.undo(env.suggestions.listing(status=SUGGESTION_ACCEPTED)["items"][0]["id"])
        (back,) = env.glossary().terms
        self.assertEqual((back.target, back.note), ("old", "mine"))

    def test_undo_is_refused_when_the_glossary_changed_since(self):
        env = Env(self)
        env.propose(term())
        item = env.pending()[0]
        env.suggestions.accept(item["id"])
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: edited by hand}\n")
        with self.assertRaises(ValueError) as caught:
            env.suggestions.undo(item["id"])
        self.assertIn("no longer has this entry", str(caught.exception))
        self.assertEqual(env.glossary().terms[0].target, "edited by hand")  # the later edit is kept

    def test_only_an_accepted_suggestion_can_be_undone(self):
        env = Env(self)
        env.propose(term())
        with self.assertRaises(ValueError):
            env.suggestions.undo(env.pending()[0]["id"])

    def test_a_character_and_its_voice_are_written_and_undone_together(self):
        env = Env(self)
        card = {"speech": "hums softly"}
        env.propose(character(card=card))
        env.suggestions.accept(env.pending()[0]["id"])
        self.assertEqual([c.name for c in env.glossary().characters], ["Kyber"])
        self.assertEqual([c.speech for c in env.styles.cards("series", "bazarr-7")], ["hums softly"])
        done = env.suggestions.listing(status=SUGGESTION_ACCEPTED)["items"][0]
        env.suggestions.undo(done["id"])
        self.assertEqual(env.glossary().characters, ())
        self.assertEqual(env.styles.cards("series", "bazarr-7"), [])

    def test_a_card_added_after_the_accept_stops_the_undo_before_anything_is_written(self):
        env = Env(self)
        env.propose(character(card={"speech": "hums"}))
        item = env.pending()[0]
        env.suggestions.accept(item["id"])
        env.style.save_card(env.series, {"card": {"name": "Kyber", "speech": "changed"}})
        with self.assertRaises(ValueError):
            env.suggestions.undo(item["id"])
        self.assertEqual(len(env.glossary().characters), 1)  # neither half was undone

    def test_the_voice_of_a_character_counts_in_the_stale_number(self):
        env = Env(self)
        env.propose(character(card={"speech": "hums"}))
        item = env.pending()[0]
        asked = env.suggestions.accept(item["id"], dry_run=True)
        self.assertEqual(asked["would_stale"], 2)


class EverySourceTests(unittest.TestCase):
    def test_21h_every_source_goes_through_the_same_accept(self):
        env = Env(self)
        for number, source in enumerate(SOURCES):
            env.propose(term(source=f"T{number}", target=f"t{number}"), source=source)
        self.assertEqual(sorted(i["source"] for i in env.pending()), sorted(SOURCES))
        for item in env.pending():
            env.suggestions.accept(item["id"])
        self.assertEqual(len(env.glossary().terms), len(SOURCES))
        self.assertEqual(sorted(i["source"] for i in env.suggestions.listing(status=SUGGESTION_ACCEPTED)["items"]), sorted(SOURCES))

    def test_the_listing_filters_by_series_status_source_and_language(self):
        env = Env(self)
        other = env.store.upsert_series("bazarr-8", title="Other")
        env.propose(term(), source="facts")
        env.propose(rule(), source="memory")
        env.suggestions.propose(other, "vi", [term(source="Zed", target="Zed")], source="research")
        self.assertEqual(len(env.suggestions.listing(series_id=env.series)["items"]), 2)
        self.assertEqual([i["source"] for i in env.suggestions.listing(source="memory")["items"]], ["memory"])
        self.assertEqual(len(env.suggestions.listing(lang="fr")["items"]), 0)
        with self.assertRaises(ValueError):
            env.suggestions.listing(status="maybe")
        with self.assertRaises(ValueError):
            env.suggestions.listing(source="rumour")


class BadgeTests(unittest.TestCase):
    def test_21g_the_badge_is_the_number_of_pending_and_follows_every_decision(self):
        env = Env(self)
        self.assertEqual(env.suggestions.summary()["pending"], 0)  # nothing: the nav hides the badge
        env.propose(term(), rule(), character())
        other = env.store.upsert_series("bazarr-8", title="Other")
        env.suggestions.propose(other, "vi", [term(source="Zed", target="Zed")], source="facts")
        summary = env.suggestions.summary()
        self.assertEqual(summary["pending"], 4)
        self.assertEqual(sorted(s["pending"] for s in summary["series"]), [1, 3])
        first, second, *_ = env.pending(series_id=env.series)
        env.suggestions.accept(first["id"])
        env.suggestions.reject(second["id"])
        self.assertEqual(env.suggestions.summary()["pending"], 2)
        env.suggestions.undo(first["id"])
        self.assertEqual(env.suggestions.summary()["pending"], 3)


class RouteTests(unittest.TestCase):
    def call(self, env, method, path, query=None, body=None):
        return suggestion_api.handle(env.suggestions, method, path, query or {}, body)

    def test_the_routes_serve_the_inbox_and_answer_400_and_404(self):
        env = Env(self)
        env.propose(term(), rule())
        for path in ("/api/suggestions", "/api/suggestions/summary", "/api/suggestions/3/accept"):
            self.assertTrue(suggestion_api.handles(path), path)
        self.assertFalse(suggestion_api.handles("/api/suggestionsx"))

        status, listing = self.call(env, "GET", "/api/suggestions", {"status": ["pending"], "series": [str(env.series)]})
        self.assertEqual((status, len(listing["items"]), listing["pending"]), (200, 2, 2))
        self.assertEqual(self.call(env, "GET", "/api/suggestions", {"status": ["maybe"]})[0], 400)
        self.assertEqual(self.call(env, "GET", "/api/suggestions", {"series": ["x"]})[0], 400)
        self.assertEqual(self.call(env, "GET", "/api/suggestions/summary")[1]["pending"], 2)

        first, second = (i["id"] for i in listing["items"])
        asked = self.call(env, "POST", f"/api/suggestions/{first}/accept", body={"dry_run": True})
        self.assertEqual((asked[0], asked[1]["saved"]), (200, False))
        edited = self.call(env, "POST", f"/api/suggestions/{first}/accept", body={"payload": {**TERM, "target": "đá Kyber"}})
        self.assertEqual((edited[0], edited[1]["saved"]), (200, True))
        self.assertEqual(env.glossary().terms[0].target, "đá Kyber")
        self.assertEqual(self.call(env, "POST", f"/api/suggestions/{first}/accept")[0], 400)  # already decided
        self.assertEqual(self.call(env, "POST", "/api/suggestions/999/accept")[0], 404)
        self.assertEqual(self.call(env, "POST", f"/api/suggestions/{second}/reject")[1]["suggestion"]["status"], "rejected")
        self.assertEqual(self.call(env, "POST", f"/api/suggestions/{first}/undo")[1]["suggestion"]["status"], "pending")
        self.assertIsNone(self.call(env, "DELETE", f"/api/suggestions/{first}/accept"))

    def test_bulk_needs_a_series_and_the_confirmed_count(self):
        env = Env(self)
        env.propose(term(), rule())
        self.assertEqual(self.call(env, "POST", "/api/suggestions/bulk", body={})[0], 400)
        self.assertEqual(self.call(env, "POST", "/api/suggestions/bulk", body={"series_id": env.series})[0], 400)
        self.assertEqual(self.call(env, "POST", "/api/suggestions/bulk", body={"series_id": env.series, "expect": 5})[0], 400)
        self.assertEqual(self.call(env, "POST", "/api/suggestions/bulk", body={"series_id": 999, "expect": 1})[0], 404)
        asked = self.call(env, "POST", "/api/suggestions/bulk", body={"series_id": env.series, "dry_run": True})[1]
        self.assertEqual((asked["count"], asked["saved"]), (2, False))
        done = self.call(env, "POST", "/api/suggestions/bulk", body={"series_id": env.series, "expect": 2})[1]
        self.assertEqual((done["count"], done["failed"]), (2, []))

    def test_r18_5_change_from_and_existing_pair_address_rules_never_auto_accept(self):
        env = Env(self, settings={"auto_accept_address_rules": True})
        # Admin already has a rule for Kyber <-> Jinx.
        owner = env.library.series_owner(env.series)
        env.glossaries.save_doc(owner.kind, owner.key, "vi", {
            "version": 1,
            "address_rules": [{"a": "Kyber", "b": "Jinx", "a_to_b": "anh–em", "b_to_a": "em–anh"}],
        })
        change = Proposal("address_rule", {
            "a": "Kyber", "b": "Jinx", "a_to_b": "anh–em", "b_to_a": "em–anh", "change_from": "S02E01",
            "stages": [
                {"a_to_b": "anh–em", "b_to_a": "em–anh", "until": "S02E01"},
                {"a_to_b": "tôi–cô", "b_to_a": "tôi–anh", "from": "S02E01"},
            ],
        })
        report = env.propose(change)
        self.assertEqual((report.added, report.auto_accepted), (1, 0))
        self.assertEqual(len(env.pending()), 1)
        # Admin's existing glossary rule is untouched until admin accepts.
        self.assertEqual(len(env.glossary().address_rules), 1)

    def test_r18_8_and_r18_9_bulk_isolates_language_commit_error_and_reports_single_stale_total(self):
        recorded: list[tuple[str, dict]] = []

        class SpyTelemetry:
            def event(self, kind, **fields):
                recorded.append((kind, fields))

            def signal(self, *a, **kw):
                pass

            def llm_call(self, *a, **kw):
                pass

        env = Env(self)
        env.suggestions.telemetry = SpyTelemetry()
        env.suggestions.propose(env.series, "en", [term(source="A", target="a")], source="facts")
        env.suggestions.propose(env.series, "vi", [term(source="B", target="b"), term(source="C", target="c")], source="facts")

        # First: normal bulk over 2 vi items + 1 en item -> per-item events omit `stale`, bulk event carries exact total.
        orig_commit = env.suggestions._commit
        def fail_vi(owner, lang, folded, *, note):
            if lang == "vi":
                raise ValueError("glossary invalid for vi")
            return orig_commit(owner, lang, folded, note=note)

        env.suggestions._commit = fail_vi  # type: ignore[method-assign]
        res = env.suggestions.bulk(env.series, expect=3)
        self.assertEqual(res["ok"], False)
        self.assertEqual(res["count"], 1)  # `en` succeeded
        self.assertEqual(len(res["failed"]), 2)  # both `vi` items recorded in failed
        self.assertTrue(all("glossary invalid for vi" in f["error"] for f in res["failed"]))
        item_events = [fields for kind, fields in recorded if kind == "suggestion.accepted"]
        bulk_events = [fields for kind, fields in recorded if kind == "suggestions.bulk_accepted"]
        self.assertEqual(len(item_events), 1)
        self.assertNotIn("stale", item_events[0])
        self.assertEqual(len(bulk_events), 1)
        self.assertEqual(bulk_events[0]["count"], 1)
        self.assertEqual(bulk_events[0]["stale"], res["would_stale"])


class NormaliseTests(unittest.TestCase):
    def test_a_payload_is_stored_as_the_glossary_would_write_it(self):
        self.assertEqual(normalise("term", {"source": " A ", "target": "b", "junk": 1, "locked": False}), {"source": "A", "target": "b"})
        self.assertEqual(
            normalise("character", {"name": "Vi", "card": {"speech": "blunt", "name": "other"}}),
            {"name": "Vi", "card": {"name": "Vi", "speech": "blunt"}},  # the card is the character's
        )
        for kind, payload in (("term", []), ("character", {"name": "Vi", "card": 3}), ("character", {"name": "Vi", "card": {"nonsense": 1}}),
                              ("term", {"source": "a", "target": "b", "from": "tomorrow"})):
            with self.subTest(kind=kind, payload=payload), self.assertRaises(ValueError):
                normalise(kind, payload)

    def test_the_fingerprint_is_blind_to_case_and_spacing_but_not_to_language_or_kind(self):
        a = fingerprint(1, "vi", "term", {"source": "Kyber", "target": "Đá"})
        self.assertEqual(a, fingerprint(1, "vi", "term", {"source": " kyber", "target": "đá  "}))
        self.assertNotEqual(a, fingerprint(1, "fr", "term", {"source": "Kyber", "target": "Đá"}))
        self.assertNotEqual(a, fingerprint(2, "vi", "term", {"source": "Kyber", "target": "Đá"}))

    def test_independent_sources_count_hosts_not_pages(self):
        self.assertEqual(independent_sources(["https://a.example/1", "https://a.example/2", "https://www.a.example/3"]), 1)
        self.assertEqual(independent_sources(["https://a.example/1", "https://b.example/1", "", None]), 2)
        self.assertEqual(independent_sources([]), 0)


class StoreTests(unittest.TestCase):
    def test_the_unique_key_is_series_language_and_fingerprint(self):
        env = Env(self)
        first = env.store.suggestion_add(env.series, "vi", "term", "facts", "fp", TERM, [], [])
        self.assertIsNotNone(first)
        self.assertIsNone(env.store.suggestion_add(env.series, "vi", "term", "research", "fp", TERM, [], []))
        self.assertIsNotNone(env.store.suggestion_add(env.series, "fr", "term", "facts", "fp", TERM, [], []))

    def test_settings_have_their_defaults_and_read_text_as_switches(self):
        from subserver.domain.config import normalize_app_config

        defaults = normalize_app_config({})
        self.assertEqual(
            (defaults["auto_accept_terms"], defaults["auto_accept_address_rules"], defaults["auto_accept_characters"]),
            (True, False, False),
        )
        changed = normalize_app_config({"auto_accept_terms": "off", "auto_accept_characters": "true"})
        self.assertEqual((changed["auto_accept_terms"], changed["auto_accept_characters"]), (False, True))
        self.assertTrue(g.Glossary().is_empty())


if __name__ == "__main__":
    unittest.main()
