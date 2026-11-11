"""Acceptance tests for SPEC §6 criteria 3, 4, 5 (fake provider only; NFR-8)."""

import json
import unittest

from subcore import glossary as g
from subcore import srt
from subcore.providers import FakeProvider
from subcore.providers.base import BadOutput, Capabilities, RateLimited
from subcore.providers.fake import echo_items
from subcore.translate import PromptContext, TranslationError, translate
from subcore.translate.planner import plan_parts

from .helpers import make_cues, numbered, synthetic_srt


def drop(ids, on_calls=None):
    """Handler: echo translation but omit ``ids`` (on the given 1-based calls, default all)."""

    def handler(req, call_no):
        items = echo_items(req, "[vi] ")
        if on_calls is None or call_no in on_calls:
            items = [it for it in items if it["i"] not in ids]
        return items

    return handler


class Criterion3Format(unittest.TestCase):
    """50 SRT files: same cue count, byte-identical timing lines, balanced tags."""

    def test_fifty_awkward_files(self):
        for seed in range(50):
            with self.subTest(seed=seed):
                source = srt.parse(synthetic_srt(seed))
                provider = FakeProvider()
                result = translate(provider, source, PromptContext(target_lang="vi"))
                out = srt.parse(srt.serialize(result.cues))

                self.assertEqual(len(out), len(source))
                for before, after in zip(source, out):
                    self.assertEqual(after.timing, before.timing)
                    self.assertEqual(after.index, before.index)
                    if before.text.strip():
                        self.assertEqual(srt.tag_problems(before.text, after.text), [])
                        self.assertTrue(after.text.startswith("[vi] "))
                    else:
                        self.assertEqual(after.text, "")
                for req in provider.calls:  # FR-3.1: nothing but {i, text} reaches the model
                    self.assertNotIn("-->", req.user + req.system)
                    for timing in (c.timing for c in source[:20]):
                        self.assertNotIn(timing, req.user)

    def test_empty_cues_never_sent_to_model(self):
        cues = make_cues(["Hello", "", "   ", "World"])
        provider = FakeProvider()
        result = translate(provider, cues)
        self.assertEqual(provider.calls[0].meta["targets"], [1, 4])
        self.assertEqual([c.text for c in result.cues], ["[vi] Hello", "", "   ", "[vi] World"])


class Criterion4Repair(unittest.TestCase):
    def test_three_missing_cues_cost_exactly_one_repair_call_with_those_cues(self):
        cues = numbered(10)
        provider = FakeProvider(drop({3, 5, 7}, on_calls={1}))
        result = translate(provider, cues)

        self.assertEqual(len(provider.calls), 2)
        first, repair = provider.calls
        self.assertEqual(first.meta["targets"], list(range(1, 11)))
        self.assertEqual(repair.meta["kind"], "repair")
        self.assertEqual(repair.meta["targets"], [3, 5, 7])
        self.assertIn("TASK: translate ONLY these items and return one {\"i\", \"text\"} object for each, in order: 3, 5, 7", repair.user)
        self.assertIn("FIX THESE", repair.user)
        # neighbours are context, but only 3, 5, 7 are requested
        self.assertTrue({2, 4, 6, 8}.issubset(repair.meta["source"]))
        self.assertTrue(all(c.text.startswith("[vi] ") for c in result.cues))
        self.assertEqual([c.kind for c in result.calls], ["translate", "repair"])

    def test_invalid_tags_are_repaired_not_accepted(self):
        cues = make_cues(["<i>One</i>", "Two"])

        def handler(req, n):
            items = echo_items(req)
            if n == 1:
                items[0]["text"] = "<i>Một"  # unclosed
            return items

        provider = FakeProvider(handler)
        result = translate(provider, cues)
        self.assertEqual(provider.calls[1].meta["targets"], [1])
        self.assertIn("tags differ", provider.calls[1].meta["notes"][1])
        self.assertEqual(result.cues[0].text, "<i>One</i>")

    def test_unparseable_reply_triggers_repair_of_everything(self):
        provider = FakeProvider(lambda req, n: "sorry, I cannot do that" if n == 1 else echo_items(req))
        result = translate(provider, numbered(4))
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[1].meta["targets"], [1, 2, 3, 4])
        self.assertEqual(len(result.cues), 4)

    def test_bad_output_exception_counts_as_empty_reply(self):
        def handler(req, n):
            if n == 1:
                raise BadOutput("blocked")
            return echo_items(req)

        provider = FakeProvider(handler)
        result = translate(provider, numbered(3))
        self.assertEqual([c.outcome for c in result.calls], ["bad_output", "ok"])

    def test_json_in_fences_and_string_ids_are_tolerated(self):
        def handler(req, n):
            body = json.dumps([{"i": str(i), "text": f"T{i}"} for i in req.meta["targets"]])
            return "```json\n" + body + "\n```"

        result = translate(FakeProvider(handler), numbered(3))
        self.assertEqual([c.text for c in result.cues], ["T1", "T2", "T3"])

    def test_extra_and_duplicate_ids_are_ignored(self):
        def handler(req, n):
            items = echo_items(req, "[vi] ")
            return items + [{"i": 99, "text": "ghost"}, {"i": 1, "text": "dupe"}]

        provider = FakeProvider(handler)
        result = translate(provider, numbered(2))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result.cues[0].text, "[vi] line 1")

    def test_persistent_failure_splits_then_succeeds(self):
        # ids 4 and 5 vanish whenever they are asked for together
        def handler(req, n):
            items = echo_items(req, "[vi] ")
            if len(req.meta["targets"]) > 1:
                items = [it for it in items if it["i"] not in (4, 5)]
            return items

        provider = FakeProvider(handler)
        result = translate(provider, numbered(10))
        self.assertEqual([c.kind for c in result.calls], ["translate", "repair", "repair", "split", "split"])
        self.assertEqual([c.meta["targets"] for c in provider.calls[3:]], [[4], [5]])
        self.assertTrue(all(c.text.startswith("[vi] ") for c in result.cues))

    def test_unfixable_cues_fail_the_job_with_their_ids(self):
        provider = FakeProvider(drop({2}))
        with self.assertRaises(TranslationError) as ctx:
            translate(provider, numbered(5))
        self.assertEqual(list(ctx.exception.failed), [2])

    def test_provider_errors_propagate(self):
        def handler(req, n):
            raise RateLimited("slow down", retry_after=12)

        with self.assertRaises(RateLimited):
            translate(FakeProvider(handler), numbered(3))


class Criterion5Glossary(unittest.TestCase):
    GLOSSARY = g.loads(
        "target_lang: vi\n"
        "terms:\n"
        "  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n"
        "  - {source: Jedi, target: Jedi}\n"
        "characters:\n  - {name: Alice, gender: f}\n"
    )

    def cues(self):
        return make_cues(["The Kyber crystal glows.", "Nothing here.", "A kyber crystal again."])

    def test_system_prompt_carries_the_locked_term(self):
        provider = FakeProvider()
        translate(provider, self.cues(), PromptContext(glossary=self.GLOSSARY))
        system = provider.calls[0].system
        self.assertIn('"Kyber crystal" → "tinh thể Kyber" [LOCKED]', system)
        self.assertLess(system.index("Core rules"), system.index("Glossary"))

    def test_violation_is_repaired_so_locked_term_is_always_present(self):
        def handler(req, n):
            items = echo_items(req, "")
            if n == 1:  # the model ignores the glossary
                return [{"i": it["i"], "text": it["text"].replace("Kyber crystal", "pha lê").replace("kyber crystal", "pha lê")} for it in items]
            return [{"i": it["i"], "text": it["text"].replace("Kyber crystal", "tinh thể Kyber").replace("kyber crystal", "tinh thể Kyber")} for it in items]

        provider = FakeProvider(handler)
        result = translate(provider, self.cues(), PromptContext(glossary=self.GLOSSARY))

        self.assertEqual(provider.calls[1].meta["targets"], [1, 3])
        self.assertIn('must be translated as "tinh thể Kyber"', provider.calls[1].meta["notes"][1])
        for cue, out in zip(self.cues(), result.cues):
            if "kyber crystal" in cue.text.lower():
                self.assertIn("tinh thể kyber", out.text.lower())

    def test_stubborn_model_never_fails_the_job_over_a_term(self):
        provider = FakeProvider(lambda req, n: [{"i": i, "text": "pha lê"} for i in req.meta["targets"]])
        result = translate(provider, self.cues(), PromptContext(glossary=self.GLOSSARY))
        self.assertEqual(len(result.cues), 3)
        self.assertTrue(any("glossary issue" in w for w in result.warnings))

    def test_timed_address_rule_reaches_the_prompt_as_item_number(self):
        gl = g.loads("address_rules:\n  - {a: Alice, b: Bob, a_to_b: 'em–anh', from: 'S01E06 00:00:04'}\n")
        provider = FakeProvider()
        translate(provider, numbered(10), PromptContext(glossary=gl, season=1, episode=6))
        self.assertIn('Alice speaking to Bob: "em–anh" (from item 3 on)', provider.calls[0].system)
        other = FakeProvider()
        translate(other, numbered(10), PromptContext(glossary=gl, season=1, episode=5))
        self.assertNotIn("Alice speaking to Bob", other.calls[0].system)


class PromptTests(unittest.TestCase):
    def test_sections_are_ordered_by_priority(self):
        gl = g.loads("terms:\n  - {source: A, target: B}\n")
        ctx = PromptContext(
            glossary=gl, title="Show", overview="Plot.", season=1, episode=2,
            extra_sections=[("Story so far", "Earlier things.")],
        )
        provider = FakeProvider()
        translate(provider, numbered(2), ctx)
        system = provider.calls[0].system
        order = ["Core rules", "Target-language rules", "Glossary", "Story so far", "Metadata"]
        positions = [system.index(f". {name}") for name in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Title: Show", system)

    def test_request_asks_for_structured_output(self):
        provider = FakeProvider()
        translate(provider, numbered(2))
        schema = provider.calls[0].response_schema
        self.assertEqual(schema["items"]["required"], ["i", "text"])

    def test_no_structured_output_capability_drops_schema(self):
        provider = FakeProvider(caps=Capabilities(structured_output=False))
        translate(provider, numbered(2))
        self.assertIsNone(provider.calls[0].response_schema)


class MultiPartTests(unittest.TestCase):
    def test_every_part_receives_the_whole_source(self):
        cues = numbered(450, "word " * 12)
        provider = FakeProvider(caps=Capabilities(max_output_tokens=9_000))
        result = translate(provider, cues)
        self.assertGreater(len(provider.calls), 1)
        covered = []
        for req in provider.calls:
            self.assertEqual(len(req.meta["source"]), 450)  # FR-2.2: full context per part
            covered += req.meta["targets"]
        self.assertEqual(sorted(covered), list(range(1, 451)))
        self.assertEqual(len(result.cues), 450)


class PlannerTests(unittest.TestCase):
    def test_single_part_when_output_fits(self):
        cues = numbered(300)
        self.assertEqual(plan_parts(cues, list(range(1, 301)), 65_536), [list(range(1, 301))])

    def test_cuts_at_the_largest_silence_near_the_middle(self):
        cues = []
        for n in range(1, 451):
            start = (n - 1) * 2000 + (10_000 if n > 210 else 0)  # long gap between cue 210 and 211
            end = start + 1500
            cues.append(srt.Cue(n, start, end, "x" * 32, f"{n}"))
        parts = plan_parts(cues, list(range(1, 451)), 9_500)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], list(range(1, 211)))

    def test_parts_respect_minimum_size(self):
        cues = numbered(150, "x" * 400)
        parts = plan_parts(cues, list(range(1, 151)), 2_000)
        self.assertEqual(len(parts), 1)  # 150 cues cannot make two parts of >= 100

    def test_force_parts_may_go_below_minimum(self):
        cues = numbered(6)
        parts = plan_parts(cues, [1, 2, 3, 4, 5, 6], 65_536, force_parts=3)
        self.assertEqual(len(parts), 3)
        self.assertEqual(sorted(sum(parts, [])), [1, 2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
