"""P3 engine support for SPEC FR-11 / criterion 7: windowed terms, ranges and locked cues (fake provider only)."""

import unittest

from subcore import glossary as g
from subcore.providers import FakeProvider
from subcore.translate import PromptContext, translate
from subcore.translate.prompt import build_system

from .helpers import numbered

FORWARD = """
terms:
  - source: Kyber crystal
    target: tinh thể Kyber
    locked: true
    from: S01E03
"""


class TermWindowTests(unittest.TestCase):
    """Criterion 7, second half: a `forward` term at episode 3 reaches episode 4 but not episode 2."""

    def system_for(self, episode):
        ctx = PromptContext(target_lang="vi", glossary=g.loads(FORWARD), season=1, episode=episode)
        return build_system(ctx)

    def test_term_from_episode_three_is_absent_from_episode_two_and_present_from_three(self):
        self.assertNotIn("Kyber crystal", self.system_for(2))
        self.assertIn("Kyber crystal", self.system_for(3))
        self.assertIn("Kyber crystal", self.system_for(4))

    def test_until_is_exclusive(self):
        glossary = g.loads("terms:\n  - {source: A, target: B, from: S01E02, until: S01E04}\n")
        in_effect = [e for e in range(1, 6) if glossary.for_episode(1, e).terms]
        self.assertEqual(in_effect, [2, 3])

    def test_season_boundary_and_unknown_episode(self):
        glossary = g.loads(FORWARD)
        self.assertTrue(glossary.for_episode(2, 1).terms)  # a later season is after S01E03
        self.assertFalse(glossary.for_episode(0, 9).terms)  # specials come before S01E03
        self.assertTrue(glossary.for_episode(None, None).terms)  # unknown position: everything applies

    def test_locked_term_outside_its_window_is_not_enforced(self):
        cues = numbered(3, "the Kyber crystal {n}")
        provider = FakeProvider(prefix="")  # echo: never contains the target
        result = translate(provider, cues, PromptContext(glossary=g.loads(FORWARD), season=1, episode=2))
        self.assertEqual(len(provider.calls), 1)  # no repair for a term that does not apply yet
        self.assertEqual(result.warnings, [])

    def test_locked_term_inside_its_window_is_enforced(self):
        cues = numbered(2, "the Kyber crystal {n}")
        provider = FakeProvider(prefix="")
        result = translate(provider, cues, PromptContext(glossary=g.loads(FORWARD), season=1, episode=4))
        self.assertGreater(len(provider.calls), 1)
        self.assertTrue(result.warnings)

    def test_window_roundtrips_through_yaml(self):
        glossary = g.loads(FORWARD)
        self.assertEqual(glossary.terms[0].start, g.Position(1, 3, 0))
        self.assertEqual(g.loads(g.dumps(glossary)), glossary)
        with self.assertRaisesRegex(g.GlossaryError, "must be after"):
            g.loads("terms:\n  - {source: A, target: B, from: S01E03, until: S01E02}\n")

    def test_same_term_in_two_windows_coexists_and_with_term_replaces_only_its_own(self):
        glossary = g.loads(FORWARD)
        glossary = glossary.with_term(g.Term("Kyber crystal", "pha lê Kyber", start=g.Position(1, 6, 0)))
        self.assertEqual([t.target for t in glossary.terms], ["tinh thể Kyber", "pha lê Kyber"])
        glossary = glossary.with_term(g.Term("kyber crystal", "đá Kyber", start=g.Position(1, 3, 0)))
        self.assertEqual(sorted(t.target for t in glossary.terms), ["pha lê Kyber", "đá Kyber"])
        self.assertEqual(len(g.merge(glossary, glossary).terms), 2)

    def test_with_address_rule_replaces_same_pair_and_start(self):
        rule = g.AddressRule("Alice", "Bob", a_to_b="em–anh", start=g.Position(1, 3, 0))
        glossary = g.Glossary().with_address_rule(rule)
        glossary = glossary.with_address_rule(g.AddressRule("alice", "bob", a_to_b="tôi–anh", start=g.Position(1, 3, 0)))
        self.assertEqual([r.a_to_b for r in glossary.address_rules], ["tôi–anh"])


class RangeAndKeepTests(unittest.TestCase):
    """FR-11.3.2 (range keeps the whole source as context) and FR-11.2.4 (locked cues)."""

    def test_range_requests_only_those_cues_but_sends_the_whole_source(self):
        cues = numbered(10)
        provider = FakeProvider()
        result = translate(provider, cues, only=[4, 5, 6])

        self.assertEqual(len(provider.calls), 1)
        request = provider.calls[0]
        self.assertEqual(request.meta["targets"], [4, 5, 6])
        self.assertEqual(sorted(request.meta["source"]), list(range(1, 11)))
        self.assertIn("in order: 4-6", request.user)
        self.assertEqual([c.text for c in result.cues][3:6], ["[vi] line 4", "[vi] line 5", "[vi] line 6"])
        # Everything else is untouched source text unless the caller says what to keep.
        self.assertEqual(result.cues[0].text, "line 1")

    def test_keep_is_copied_verbatim_never_requested_and_shown_as_reference(self):
        cues = numbered(10)
        provider = FakeProvider()
        result = translate(provider, cues, keep={10: "Câu đã sửa tay\nhai dòng"})

        self.assertEqual(provider.calls[0].meta["targets"], list(range(1, 10)))
        self.assertEqual(result.cues[9].text, "Câu đã sửa tay\nhai dòng")  # criterion 7, first half
        self.assertIn("ALREADY TRANSLATED", provider.calls[0].user)
        self.assertIn("Câu đã sửa tay", provider.calls[0].user)
        self.assertEqual(result.cues[9].timing, cues[9].timing)
        self.assertEqual(result.cues[0].text, "[vi] line 1")

    def test_range_with_keep_outside_it_preserves_the_current_translation(self):
        cues = numbered(6)
        current = {n: f"cũ {n}" for n in range(1, 7)}
        outside = {n: text for n, text in current.items() if n not in (3, 4)}
        provider = FakeProvider()
        result = translate(provider, cues, only=[3, 4], keep=outside)

        self.assertEqual(provider.calls[0].meta["targets"], [3, 4])
        self.assertEqual([c.text for c in result.cues], ["cũ 1", "cũ 2", "[vi] line 3", "[vi] line 4", "cũ 5", "cũ 6"])

    def test_nothing_to_translate_makes_no_call(self):
        cues = numbered(2)
        provider = FakeProvider()
        result = translate(provider, cues, keep={1: "một", 2: "hai"})
        self.assertEqual(provider.calls, [])
        self.assertEqual([c.text for c in result.cues], ["một", "hai"])

    def test_kept_cue_that_is_not_in_the_range_is_not_requested_even_if_listed(self):
        cues = numbered(5)
        provider = FakeProvider()
        translate(provider, cues, only=[2, 3, 4], keep={3: "ba"})
        self.assertEqual(provider.calls[0].meta["targets"], [2, 4])


if __name__ == "__main__":
    unittest.main()
