import unittest

from subcore import glossary as g

from .helpers import numbered

YAML = """
version: 1
target_lang: vi
style: informal
terms:
  - source: Kyber crystal
    target: tinh thể Kyber
    aliases: [Kyber crystals]
    locked: true
  - source: Jedi
    target: Jedi
characters:
  - name: Alice
    gender: f
address_rules:
  - a: Alice
    b: Bob
    a_to_b: "em–anh"
    from: "S01E06 00:00:20"
    until: "S02E01"
"""


class LoadTests(unittest.TestCase):
    def test_loads_all_sections(self):
        gl = g.loads(YAML)
        self.assertEqual(gl.target_lang, "vi")
        self.assertEqual(len(gl.terms), 2)
        self.assertTrue(gl.terms[0].locked)
        self.assertEqual(gl.terms[0].sources, ("Kyber crystal", "Kyber crystals"))
        self.assertEqual(gl.address_rules[0].start, g.Position(1, 6, 20000))

    def test_empty_document_is_empty_glossary(self):
        self.assertTrue(g.loads("").is_empty())

    def test_errors_name_the_path(self):
        cases = {
            "terms:\n  - source: A\n": "terms[0]: missing required field 'target'",
            "terms: nope\n": "terms: expected a list",
            "address_rules:\n  - {a: A, b: B}\n": "needs 'a_to_b'",
            "address_rules:\n  - {a: A, b: B, a_to_b: x, from: 'E6'}\n": "address_rules[0].from",
            "address_rules:\n  - {a: A, b: B, a_to_b: x, from: S02E01, until: S01E01}\n": "must be after",
            "terms:\n  - {source: A, target: B, locked: yes please}\n": "locked",
            "version: 2\n": "unsupported glossary version",
            "- a\n- b\n": "top level",
        }
        for text, expected in cases.items():
            with self.subTest(text=text), self.assertRaises(g.GlossaryError) as ctx:
                g.loads(text)
            self.assertIn(expected, str(ctx.exception))

    def test_dumps_roundtrip(self):
        gl = g.loads(YAML)
        self.assertEqual(g.loads(g.dumps(gl)), gl)


class MergeTests(unittest.TestCase):
    def test_later_wins_and_unions(self):
        a = g.loads("style: a\nterms:\n  - {source: X, target: old}\n  - {source: Y, target: y}\n")
        b = g.loads("style: b\nterms:\n  - {source: x, target: new, locked: true}\n")
        merged = g.merge(a, b)
        self.assertEqual(merged.style, "b")
        by_source = {t.source.casefold(): t for t in merged.terms}
        self.assertEqual(by_source["x"].target, "new")
        self.assertTrue(by_source["x"].locked)
        self.assertEqual(by_source["y"].target, "y")


class ActiveRuleTests(unittest.TestCase):
    def setUp(self):
        self.gl = g.loads(YAML)
        self.cues = numbered(100)  # cue n starts at (n-1)*2000 ms

    def test_before_range_is_inactive(self):
        self.assertEqual(self.gl.active_address_rules(1, 5, self.cues), [])

    def test_start_inside_episode_maps_timecode_to_cue_ordinal(self):
        (rule,) = self.gl.active_address_rules(1, 6, self.cues)
        self.assertEqual(rule.from_cue, 11)  # first cue starting at/after 20 s
        self.assertIsNone(rule.until_cue)

    def test_later_episode_applies_from_the_start(self):
        (rule,) = self.gl.active_address_rules(1, 7, self.cues)
        self.assertIsNone(rule.from_cue)

    def test_until_is_exclusive(self):
        self.assertEqual(self.gl.active_address_rules(2, 1, self.cues), [])

    def test_unknown_episode_returns_everything(self):
        self.assertEqual(len(self.gl.active_address_rules(None, None)), 1)


class LockedTests(unittest.TestCase):
    def test_violation_detected_by_alias_and_case(self):
        gl = g.loads(YAML)
        bad = g.locked_violations(gl, "The kyber CRYSTALS hum.", "Những viên pha lê kêu vang.")
        self.assertEqual([t.source for t in bad], ["Kyber crystal"])

    def test_satisfied_and_absent_terms_are_fine(self):
        gl = g.loads(YAML)
        self.assertEqual(g.locked_violations(gl, "A Kyber crystal", "Một tinh thể Kyber"), [])
        self.assertEqual(g.locked_violations(gl, "Nothing here", "Không có gì"), [])

    def test_word_boundaries(self):
        gl = g.loads("terms:\n  - {source: Ant, target: Kiến, locked: true}\n")
        self.assertEqual(g.locked_violations(gl, "Important news", "Tin quan trọng"), [])


if __name__ == "__main__":
    unittest.main()
