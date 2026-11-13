"""Story memory in the engine: facts, the deterministic fold, the bundle and its budget (FR-5, ADR-6)."""

import json
import unittest

from subcore.memory import (
    Facts,
    FactsError,
    UnitFacts,
    build_context,
    compress_arc,
    extract_facts,
    fingerprint,
    fold,
)
from subcore.providers import BadOutput, FakeProvider, QuotaExhausted

from .helpers import numbered


def event(a, b, relation, cue=1, intimacy=0, hierarchy="equal", confidence=0.9, evidence=""):
    return {"a": a, "b": b, "relation": relation, "cue": cue, "intimacy": intimacy, "hierarchy": hierarchy,
            "confidence": confidence, "evidence": evidence}


def facts(summary="", characters=(), events=(), terms=()):
    return Facts.from_dict({
        "characters": list(characters), "relationship_events": list(events),
        "terms": list(terms), "summary": summary,
    })


class FactsParsingTests(unittest.TestCase):
    def test_malformed_entries_are_dropped_not_fatal(self):
        parsed = Facts.from_dict({
            "characters": [{"name": "Alice", "gender": "F"}, {"name": ""}, "Bob", {"name": "alice"}, {"name": "Al", "aliases": ["Al", "Alice"]}],
            "relationship_events": [
                event("Alice", "Bob", "romantic", cue=3, intimacy=99, confidence=7),
                event("Alice", "Alice", "friend"),            # same person
                event("Alice", "Bob", "frenemies"),           # not a known relation
                event("Alice", "Bob", "friend", cue=500),     # beyond the end of the episode
                {"a": "Alice", "b": "Bob", "relation": "friend", "cue": "x"},
            ],
            "terms": [{"source": "Kyber"}, "kyber", {"source": ""}],
            "summary": "  A  long\n summary ",
        }, cue_count=100)
        self.assertEqual([c.name for c in parsed.characters], ["Alice", "Al"])
        self.assertEqual(parsed.characters[0].gender, "f")
        self.assertEqual(parsed.characters[1].aliases, ("Alice",))  # an alias equal to the name itself is dropped
        self.assertEqual(len(parsed.relationship_events), 1)
        self.assertEqual((parsed.relationship_events[0].intimacy, parsed.relationship_events[0].confidence), (5, 1.0))
        self.assertEqual([t.source for t in parsed.terms], ["Kyber"])
        self.assertEqual(parsed.summary, "A long summary")

    def test_not_an_object_is_an_error(self):
        for bad in ([], "text", None, 3):
            with self.assertRaises(FactsError):
                Facts.from_dict(bad)

    def test_round_trip_keeps_the_hash(self):
        original = facts("s", [{"name": "A", "gender": "m"}], [event("A", "B", "friend", cue=2)], [{"source": "X"}])
        again = Facts.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(again, original)
        self.assertEqual(again.hash, original.hash)
        self.assertNotEqual(facts("different").hash, original.hash)


class ExtractTests(unittest.TestCase):
    def reply(self, **kw):
        return json.dumps({"characters": [], "relationship_events": [], "terms": [], "summary": "ok", **kw})

    def test_one_call_with_item_numbers_and_no_timecodes(self):
        provider = FakeProvider(handler=lambda req, n: self.reply())
        cues = numbered(3, "dialogue {n}")
        result = extract_facts(provider, cues, title="Show", season=1, episode=2, source_lang="en")
        self.assertEqual(result.facts.summary, "ok")
        (request,) = provider.calls
        self.assertEqual(request.task, "extract")
        self.assertIn('{"i": 2, "text": "dialogue 2"}', request.user)
        self.assertNotIn("-->", request.user)
        self.assertNotIn("00:00", request.user)
        self.assertIn("season 1, episode 2", request.user)
        self.assertIsNotNone(request.response_schema)

    def test_an_unusable_answer_is_asked_again_once_then_fails(self):
        provider = FakeProvider(handler=lambda req, n: "no json here")
        with self.assertRaises(FactsError):
            extract_facts(provider, numbered(2))
        self.assertEqual(len(provider.calls), 2)

    def test_a_second_try_can_succeed(self):
        provider = FakeProvider(handler=lambda req, n: "[]" if n == 1 else self.reply())
        self.assertEqual(extract_facts(provider, numbered(2)).facts.summary, "ok")

    def test_events_past_the_last_cue_are_discarded(self):
        provider = FakeProvider(handler=lambda req, n: self.reply(relationship_events=[event("A", "B", "friend", cue=9)]))
        self.assertEqual(extract_facts(provider, numbered(3)).facts.relationship_events, ())

    def test_quota_errors_propagate(self):
        def refuse(req, n):
            raise QuotaExhausted("daily")

        with self.assertRaises(QuotaExhausted):
            extract_facts(FakeProvider(handler=refuse), numbered(2))


class FoldTests(unittest.TestCase):
    def test_latest_event_per_pair_wins_and_order_matters(self):
        lovers = UnitFacts("S01E01", facts("one", events=[event("Alice", "Bob", "romantic", intimacy=5)]))
        enemies = UnitFacts("S01E06", facts("six", events=[event("Bob", "Alice", "enemy", intimacy=0)]))
        self.assertEqual(fold([lovers]).relationships[0].relation, "romantic")
        after = fold([lovers, enemies]).relationships
        self.assertEqual([(r.relation, r.since) for r in after], [("enemy", "S01E06")])
        # A pair is unordered: "Bob & Alice" replaced "Alice & Bob".
        self.assertEqual(len(after), 1)

    def test_within_one_unit_the_later_cue_wins(self):
        unit = UnitFacts("S01E02", facts(events=[event("A", "B", "enemy", cue=40), event("A", "B", "friend", cue=9)]))
        self.assertEqual(fold([unit]).relationships[0].relation, "enemy")

    def test_doubtful_events_are_ignored(self):
        unit = UnitFacts("S01E01", facts(events=[event("A", "B", "friend"), event("A", "B", "enemy", cue=5, confidence=0.1)]))
        self.assertEqual(fold([unit]).relationships[0].relation, "friend")

    def test_characters_merge_by_alias_and_events_follow_them(self):
        one = UnitFacts("S01E01", facts(characters=[{"name": "Elizabeth", "aliases": ["Liz"], "gender": "f"}],
                                        events=[event("Liz", "Bob", "friend")]))
        two = UnitFacts("S01E02", facts(characters=[{"name": "Liz", "aliases": ["Beth"], "age_hint": "20s"}],
                                        events=[event("Elizabeth", "Bob", "romantic")]))
        state = fold([one, two])
        self.assertEqual([c.name for c in state.characters if c.name != "Bob"], ["Elizabeth"])
        merged = next(c for c in state.characters if c.name == "Elizabeth")
        self.assertEqual((merged.gender, merged.age_hint, set(merged.aliases)), ("f", "20s", {"Liz", "Beth"}))
        self.assertEqual(len(state.relationships), 1)
        self.assertEqual(state.relationships[0].relation, "romantic")

    def test_two_characters_of_one_episode_are_never_merged_by_a_shared_nickname(self):
        # Seen on real output: a teenager and his grandfather both listed with the alias "Podolski".
        unit = UnitFacts("S01E02", facts(
            characters=[{"name": "Trevor Podolski", "aliases": ["Podolski"]},
                        {"name": "Deputy Commissioner Podolski", "aliases": ["Podolski"]}],
            events=[event("Holt", "Deputy Commissioner Podolski", "enemy")],
        ))
        state = fold([unit])
        self.assertEqual(sorted(c.name for c in state.characters),
                         ["Deputy Commissioner Podolski", "Holt", "Trevor Podolski"])
        self.assertEqual({r.b for r in state.relationships}, {"Deputy Commissioner Podolski"})

    def test_a_doubtful_reading_does_not_overturn_a_clear_one(self):
        clear = UnitFacts("S01E03", facts(events=[event("Jake", "Charles", "friend", intimacy=4, confidence=1.0)]))
        doubtful = UnitFacts("S01E04", facts(events=[event("Jake", "Charles", "professional", confidence=0.5)]))
        sure = UnitFacts("S01E05", facts(events=[event("Jake", "Charles", "rival", confidence=0.6)]))
        self.assertEqual(fold([clear, doubtful]).relationships[0].relation, "friend")
        self.assertEqual(fold([clear, doubtful, sure]).relationships[0].relation, "rival")

    def test_the_exact_relationship_is_kept_and_shown(self):
        unit = UnitFacts("S01E01", facts(events=[{**event("Gloria", "Manny", "family", hierarchy="a_above"), "detail": "mother and son"}]))
        self.assertEqual(fold([unit]).relationships[0].detail, "mother and son")
        (_, body), = build_context([], unit.facts).sections
        self.assertIn("family (mother and son)", body)
        relationship = dict(build_context([unit], None).sections)["Relationship state"]
        self.assertIn("family (mother and son)", relationship)

    def test_a_given_name_and_the_full_name_are_one_character(self):
        # Seen on real Gemini output: "Ted" in one episode, "Ted Mosby" in the next.
        one = UnitFacts("S01E01", facts(characters=[{"name": "Ted"}, {"name": "Robin"}],
                                        events=[event("Ted", "Robin", "romantic", intimacy=2)]))
        two = UnitFacts("S01E02", facts(characters=[{"name": "Ted Mosby"}, {"name": "Robin Scherbatsky"}],
                                        events=[event("Ted Mosby", "Robin Scherbatsky", "friend", intimacy=3)]))
        state = fold([one, two])
        self.assertEqual(sorted(c.name for c in state.characters), ["Robin", "Ted"])
        self.assertEqual([(r.a, r.b, r.relation) for r in state.relationships], [("Ted", "Robin", "friend")])

    def test_an_ambiguous_short_name_is_not_merged_by_guess(self):
        unit = UnitFacts("S01E01", facts(characters=[{"name": "Ted Mosby"}, {"name": "Ted Evelyn"}, {"name": "Ted"}]))
        self.assertEqual(len(fold([unit]).characters), 3)

    def test_terms_are_counted_per_unit_and_summaries_keep_story_order(self):
        units = [UnitFacts(f"S01E0{n}", facts(f"sum {n}", terms=[{"source": "Kyber"}])) for n in (1, 2, 3)]
        state = fold(units)
        self.assertEqual(state.terms, {"Kyber": 3})
        self.assertEqual([label for label, _ in state.summaries], ["S01E01", "S01E02", "S01E03"])

    def test_fold_is_deterministic(self):
        units = [UnitFacts("S01E01", facts("a", [{"name": "A"}], [event("A", "B", "friend")])),
                 UnitFacts("S01E02", facts("b", [{"name": "B"}], [event("B", "C", "rival")]))]
        self.assertEqual(fold(units), fold(list(units)))


class BundleTests(unittest.TestCase):
    def units(self, n=6, summary=lambda i: f"summary {i}"):
        return [UnitFacts(f"S01E0{i}", facts(summary(i), [{"name": "Alice"}], [event("Alice", "Bob", "friend")])) for i in range(1, n + 1)]

    def test_sections_use_only_the_units_given(self):
        context = build_context(self.units(2), facts("this episode"))
        text = "\n".join(body for _, body in context.sections)
        self.assertIn("summary 1", text)
        self.assertIn("summary 2", text)
        self.assertNotIn("summary 3", text)
        self.assertEqual([t for t, _ in context.sections], ["Story so far", "Relationship state", "This episode"])

    def test_no_history_gives_only_the_episode_itself(self):
        context = build_context([], facts("only this"))
        self.assertEqual([t for t, _ in context.sections], ["This episode"])
        self.assertEqual(build_context([], None).sections, [])

    def test_the_episode_section_lists_changes_by_item_number(self):
        own = facts("s", events=[event("Alice", "Bob", "enemy", cue=412, evidence="never again"),
                                 event("A", "B", "friend", cue=7, confidence=0.1)])
        (_, body), = build_context([], own).sections
        self.assertIn("from item 412: Alice & Bob: enemy", body)
        self.assertIn('"never again"', body)
        self.assertNotIn("from item 7", body)  # too doubtful to show

    def test_fingerprint_tracks_prior_and_own_facts(self):
        prior = self.units(3)
        own = facts("mine")
        base = fingerprint(prior, own)
        self.assertEqual(fingerprint(prior, own), base)
        self.assertNotEqual(fingerprint(prior[:2], own), base)  # a unit disappeared
        self.assertNotEqual(fingerprint(prior, facts("changed")), base)
        changed = [prior[0], UnitFacts("S01E02", facts("edited")), prior[2]]
        self.assertNotEqual(fingerprint(changed, own), base)
        self.assertNotEqual(fingerprint(prior, None), base)

    def test_within_budget_everything_is_listed_in_full(self):
        calls = []
        context = build_context(self.units(5), None, compress=lambda entries: calls.append(entries) or "x")
        self.assertEqual(calls, [])
        self.assertEqual(context.compressed, "")
        self.assertIn("S01E01: summary 1", context.sections[0][1])

    def test_over_budget_old_summaries_are_condensed_and_recent_ones_kept(self):
        long = lambda i: f"episode {i} " + "word " * 300  # noqa: E731
        seen = []

        def compress(entries):
            seen.append([label for label, _ in entries])
            return "THE ARC"

        context = build_context(self.units(8, long), None, budget_tokens=600, recent=2, compress=compress)
        self.assertEqual(seen, [[f"S01E0{i}" for i in range(1, 7)]])  # the six oldest only
        self.assertEqual(context.compressed, "model")
        story = context.sections[0][1]
        self.assertIn("THE ARC", story)
        self.assertIn("S01E07: episode 7", story)
        self.assertIn("S01E08: episode 8", story)
        self.assertNotIn("episode 3", story)

    def test_without_a_compressor_old_summaries_shrink_to_their_first_sentence(self):
        long = lambda i: f"First sentence {i}. " + "More. " * 300  # noqa: E731
        context = build_context(self.units(6, long), None, budget_tokens=600, recent=1)
        self.assertEqual(context.compressed, "truncated")
        story = context.sections[0][1]
        self.assertIn("S01E02: First sentence 2", story)
        self.assertNotIn("More. More.", story.split("S01E06")[0])

    def test_a_compressor_with_a_useless_answer_falls_back(self):
        def useless(entries):
            raise BadOutput("empty")

        long = lambda i: "plot " * 300  # noqa: E731
        context = build_context(self.units(6, long), None, budget_tokens=600, recent=1, compress=useless)
        self.assertEqual(context.compressed, "truncated")

    def test_provider_failures_are_not_swallowed_by_compression(self):
        def refuse(entries):
            raise QuotaExhausted("daily")

        long = lambda i: "plot " * 300  # noqa: E731
        with self.assertRaises(QuotaExhausted):
            build_context(self.units(6, long), None, budget_tokens=600, recent=1, compress=refuse)

    def test_the_result_is_deterministic(self):
        a = build_context(self.units(4), facts("x"))
        b = build_context(self.units(4), facts("x"))
        self.assertEqual((a.sections, a.fingerprint), (b.sections, b.fingerprint))


class CompressArcTests(unittest.TestCase):
    def test_one_summarize_call_with_the_labelled_summaries(self):
        provider = FakeProvider(handler=lambda req, n: "  The   story so far. ")
        text = compress_arc(provider, [("S01E01", "a"), ("S01E02", "b")])
        self.assertEqual(text, "The story so far.")
        (request,) = provider.calls
        self.assertEqual(request.task, "summarize")
        self.assertIn("S01E01: a", request.user)
        self.assertIsNone(request.response_schema)

    def test_an_empty_answer_is_bad_output(self):
        with self.assertRaises(BadOutput):
            compress_arc(FakeProvider(handler=lambda req, n: " "), [("S01E01", "a")])


if __name__ == "__main__":
    unittest.main()
