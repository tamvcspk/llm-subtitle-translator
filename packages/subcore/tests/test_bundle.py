"""Bundle format, hash and reading (FR-21.1, FR-21.6, criteria 18d and 18h)."""

import copy
import json
import unittest
from unittest import mock

from subcore import srt
from subcore.bundle import BUNDLE_VERSION, BundleError, TranslationBundle, matches
from subcore.glossary import AddressRule, Character, Glossary, Term, loads
from subcore.providers import FakeProvider
from subcore.translate import PromptContext, TranslateOptions
from subcore.translate.engine import make_bundle, render_requests, run
from subcore.translate.prompt import PROMPT_VERSION, render_system

from .helpers import numbered

GLOSSARY = loads(
    """
version: 1
target_lang: vi
style: "Informal"
terms:
  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}
characters:
  - {name: Alice, gender: f}
address_rules:
  - {a: Alice, b: Bob, a_to_b: "em–anh", from: "S01E01 00:00:03", until: "S01E01 00:00:06"}
"""
)


def bundle(cues=None, *, glossary=None, keep=None, only=None, title="Show"):
    cues = cues if cues is not None else numbered(4, "Alice met Bob {n}")
    context = PromptContext(target_lang="vi", title=title, season=1, episode=1, glossary=glossary)
    return make_bundle(cues, context, TranslateOptions(), only=only, keep=keep, model="m1", max_output_tokens=4000)


class BundleFormatTests(unittest.TestCase):
    def test_round_trip_keeps_content_and_hash(self):
        b = bundle(glossary=GLOSSARY, keep={2: "đã sửa"}, only=[1, 3])
        again = TranslationBundle.from_json(b.to_json())
        self.assertEqual(again.to_dict(), b.to_dict())
        self.assertEqual(again.bundle_hash(), b.bundle_hash())
        self.assertEqual(again.requested, (1, 3))
        self.assertEqual(again.keep, {2: "đã sửa"})

    def test_hash_is_stable_and_changes_with_prompt_or_parameters_only(self):
        base = bundle(glossary=GLOSSARY)
        self.assertEqual(base.bundle_hash(), bundle(glossary=GLOSSARY).bundle_hash())
        self.assertNotEqual(base.bundle_hash(), bundle(glossary=GLOSSARY, title="Other").bundle_hash())
        other_temp = make_bundle(numbered(4, "Alice met Bob {n}"), PromptContext(
            target_lang="vi", title="Show", season=1, episode=1, glossary=GLOSSARY), TranslateOptions(temperature=0.2),
            model="m1", max_output_tokens=4000)
        self.assertNotEqual(base.bundle_hash(), other_temp.bundle_hash())
        other_text = bundle(cues=numbered(4, "Alice met Bob {n}!"), glossary=GLOSSARY)
        self.assertNotEqual(base.bundle_hash(), other_text.bundle_hash())

    def test_prompt_version_is_part_of_every_bundle(self):
        self.assertEqual(bundle().params.prompt_version, PROMPT_VERSION)

    def test_bundle_holds_no_key_and_no_machine_path(self):
        b = bundle(glossary=GLOSSARY)
        data = b.to_dict()
        self.assertEqual(set(data), {
            "bundle_version", "params", "language", "episode", "glossary", "active_rules", "sections",
            "few_shot", "cues", "requested", "keep",
        })
        text = b.to_json().lower()
        for marker in ("api_key", "apikey", "password", "authorization", "c:\\", "/media/", "/state/"):
            self.assertNotIn(marker, text)

    def test_older_bundle_without_optional_fields_still_runs(self):
        """Criterion 18h: a bundle written before the optional fields existed loads and renders."""
        data = json.loads(bundle(glossary=GLOSSARY).to_json())
        for key in ("sections", "few_shot", "keep", "requested", "active_rules"):
            data.pop(key)
        data["bundle_version"] = 1
        data["written_by_a_future_field"] = {"ignored": True}
        old = TranslationBundle.from_dict(data)
        self.assertEqual(old.keep, {})
        self.assertIsNone(old.requested)
        provider = FakeProvider()
        result = run(old, provider)
        self.assertEqual([c.text for c in result.cues], [f"[vi] Alice met Bob {n}" for n in range(1, 5)])

    def test_newer_bundle_version_is_refused(self):
        data = json.loads(bundle().to_json())
        data["bundle_version"] = BUNDLE_VERSION + 1
        with self.assertRaises(BundleError):
            TranslationBundle.from_dict(data)

    def test_damaged_bundle_is_refused_with_a_reason(self):
        with self.assertRaises(BundleError):
            TranslationBundle.from_json("[]")
        with self.assertRaises(BundleError):
            TranslationBundle.from_json("{not json")
        data = json.loads(bundle().to_json())
        data["glossary"] = "terms: [unclosed"
        with self.assertRaises(BundleError):
            TranslationBundle.from_dict(data)

    def test_every_malformed_field_is_a_bundle_error(self):
        """A hand-edited bundle gives a reason, never a traceback."""
        good = json.loads(bundle(glossary=GLOSSARY).to_json())
        damage = {
            "keep without i": lambda d: d.__setitem__("keep", [{"text": "x"}]),
            "section without title": lambda d: d.__setitem__("sections", [{"body": "x"}]),
            "few_shot without target": lambda d: d.__setitem__("few_shot", [{"source": "x"}]),
            "glossary not text": lambda d: d.__setitem__("glossary", 7),
            "requested not numbers": lambda d: d.__setitem__("requested", ["a"]),
            "active rule not an object": lambda d: d.__setitem__("active_rules", [3]),
            "params not an object": lambda d: d.__setitem__("params", [1]),
            "cues not a list": lambda d: d.__setitem__("cues", 5),
        }
        for name, break_it in damage.items():
            with self.subTest(name):
                data = copy.deepcopy(good)
                break_it(data)
                with self.assertRaises(BundleError):
                    TranslationBundle.from_dict(data)

    def test_make_bundle_drops_keep_outside_the_source(self):
        b = make_bundle(numbered(2), keep={1: "a", 9: "outside"})
        self.assertEqual(b.keep, {1: "a"})


class ReadingTests(unittest.TestCase):
    def test_matches_are_read_only_and_do_not_change_the_hash(self):
        b = bundle(cues=[*numbered(2, "Kyber crystal {n}"), *numbered(2, "Alice waves {n}")], glossary=GLOSSARY)
        before = b.bundle_hash()
        snapshot = copy.deepcopy(b.to_dict())
        found = matches(b, 1, 4)
        self.assertEqual(b.bundle_hash(), before)
        self.assertEqual(b.to_dict(), snapshot)
        kinds = {(m.kind, m.label) for m in found}
        self.assertIn(("term", "Kyber crystal → tinh thể Kyber"), kinds)
        self.assertIn(("character", "Alice"), kinds)

    def test_address_rule_matches_only_its_cue_range(self):
        cues = numbered(8, "Bob says hi {n}")
        ctx = PromptContext(target_lang="vi", glossary=GLOSSARY, season=1, episode=1)
        b = make_bundle(cues, ctx)
        rules = [m for m in matches(b, 1, 8) if m.kind == "address"]
        self.assertEqual(len(rules), 1)
        self.assertLess(max(rules[0].cues), 8)  # the rule ends before the last cue
        self.assertEqual(matches(b, 6, 8), [m for m in matches(b, 6, 8) if m.kind != "address"])

    def test_matches_of_an_empty_or_reversed_range(self):
        b = bundle(glossary=GLOSSARY)
        self.assertEqual(matches(b, 3, 1), [])
        self.assertEqual(matches(b, 50, 60), [])


class PromptTests(unittest.TestCase):
    def test_few_shot_is_rendered_last_and_only_when_present(self):
        plain = bundle()
        self.assertNotIn("Examples", render_system(plain))
        with_examples = make_bundle(
            numbered(2), PromptContext(target_lang="vi", few_shot=[("Hello", "Chào")], title="Show"),
        )
        system = render_system(with_examples)
        self.assertIn('- "Hello" → "Chào"', system)
        self.assertTrue(system.rstrip().endswith('- "Hello" → "Chào"'))

    def test_render_requests_match_the_first_call_of_run(self):
        """Criterion 18a at the engine level: the requests shown are the requests sent."""
        cues = numbered(6)
        b = bundle(cues=cues, glossary=GLOSSARY, keep={3: "ba đã sửa"}, only=[2, 3, 4])
        shown = render_requests(b)
        self.assertEqual(len(shown), 1)
        provider = FakeProvider()
        run(b, provider)
        first = provider.calls[0]
        self.assertEqual(first.system, shown[0].system)
        self.assertEqual(first.user, shown[0].user)
        self.assertEqual(first.system, render_system(b))

    def test_rendering_calls_no_provider(self):
        """Criterion 18b: render-prompt never reaches a provider; a provider that would fail proves it."""
        from unittest import mock

        b = bundle(glossary=GLOSSARY)
        with mock.patch.object(FakeProvider, "generate", side_effect=AssertionError("provider called")) as gen:
            self.assertEqual(len(render_requests(b)), 1)
        gen.assert_not_called()

    def test_translate_wrapper_and_bundle_run_agree(self):
        """The wrapper makes the bundle and runs it: the same output as calling run on that bundle."""
        from subcore.translate import translate

        cues = numbered(12)
        ctx = PromptContext(target_lang="vi", glossary=GLOSSARY, season=1, episode=1)
        via_wrapper = translate(FakeProvider(), cues, ctx)
        direct = run(make_bundle(cues, ctx, model="fake-1", max_output_tokens=FakeProvider().caps.max_output_tokens),
                     FakeProvider())
        self.assertEqual(srt.serialize(via_wrapper.cues), srt.serialize(direct.cues))


class SplitTests(unittest.TestCase):
    def test_the_split_after_failed_repairs_uses_the_bundle_parameters(self):
        """The bundle says how to plan; the re-plan after a failed repair must follow it, not module defaults."""
        from subcore.providers.base import Capabilities
        from subcore.translate import TranslationError, planner

        def drop_two(req, call_no):
            return [{"i": i, "text": f"x{i}"} for i in req.meta["targets"] if i not in (3, 4)]

        b = make_bundle(numbered(6), PromptContext(), TranslateOptions(output_factor=2.5, min_part_cues=7),
                        model="m", max_output_tokens=4000)
        with mock.patch.object(planner, "plan_parts", wraps=planner.plan_parts) as plan:
            with self.assertRaises(TranslationError):
                run(b, FakeProvider(drop_two, caps=Capabilities(max_output_tokens=4000)))
        forced = [c for c in plan.call_args_list if c.kwargs.get("force_parts")]
        self.assertTrue(forced)
        for call in forced:
            self.assertEqual(call.kwargs["output_factor"], 2.5)
            self.assertEqual(call.kwargs["min_part_cues"], 7)


class GlossaryRoundTripTests(unittest.TestCase):
    def test_episode_scoped_glossary_survives_the_bundle(self):
        scoped = GLOSSARY.for_episode(1, 1)
        b = make_bundle(numbered(3), PromptContext(glossary=GLOSSARY, season=1, episode=1))
        self.assertEqual(b.glossary.terms, scoped.terms)
        self.assertEqual(b.glossary.characters, scoped.characters)
        self.assertEqual(
            [(r.a, r.b, r.a_to_b) for r in b.glossary.address_rules],
            [(r.a, r.b, r.a_to_b) for r in scoped.address_rules],
        )
        self.assertIsInstance(b.glossary, Glossary)
        self.assertIsInstance(b.glossary.terms[0], Term)
        self.assertIsInstance(b.glossary.characters[0], Character)
        self.assertIsInstance(b.glossary.address_rules[0], AddressRule)


if __name__ == "__main__":
    unittest.main()
