"""Review wave W0/W1 (PLAN, "Code review P10-P14"): the hash contracts and the one place that counts what a change makes
stale. R12-1 (the glossary hash), R12-3 (the trial builds the rules a job builds), R12-6 (preset, restore and copy say
their impact), R12-7 (a style counts every language), R12-8 (an adopted variant is not stale at once). Fakes only."""

import hashlib
import json
import unittest
from dataclasses import replace

from subcore import glossary as g
from subcore import srt

from subserver.domain.rules import build_rules

from .test_corrections import SETTINGS
from .test_style_profile import Env

# sha256 of json(["version: 1\n", []]): what an episode with an empty glossary, no correction and no style has hashed
# to since P3. A change here marks every translation of every such episode stale: do not "fix" the literal.
EMPTY_GLOSSARY_HASH = "b2646dc3809daed977e38410aef5494007b7cac61f8b794e6ebc37da03c2c0f5"
TERM = "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n"


def hash_before_p12(yaml_text: str) -> str:
    """The rules hash as it was computed when a glossary was a YAML file read as it stood (no ``target_lang`` added)."""
    payload = [g.dumps(g.loads(yaml_text)), []]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class RulesHashContractTests(unittest.TestCase):
    """W0: a hash that is already stored must keep matching (ADR-14)."""

    def test_an_empty_glossary_has_the_hash_it_always_had(self):
        self.assertEqual(build_rules(g.Glossary(), season=1, episode=1).hash, EMPTY_GLOSSARY_HASH)

    def test_the_language_is_not_part_of_the_hash(self):
        plain = g.loads(TERM)
        for glossary in (plain, g.loads("target_lang: vi\n" + TERM), g.loads("target_lang: fr\n" + TERM)):
            for lang in ("vi", "fr", ""):
                with self.subTest(lang=lang, target_lang=glossary.target_lang):
                    self.assertEqual(
                        build_rules(glossary, season=1, episode=1, lang=lang).hash,
                        hash_before_p12(TERM),
                    )
        self.assertEqual(build_rules(g.Glossary(target_lang="vi"), season=None, episode=None).hash, EMPTY_GLOSSARY_HASH)

    def test_a_rule_still_changes_the_hash(self):
        self.assertNotEqual(build_rules(g.loads(TERM), season=1, episode=1).hash, EMPTY_GLOSSARY_HASH)

    def test_a_translation_made_before_p12_is_not_stale_once_its_glossary_is_in_the_database(self):
        """The upgrade imports a YAML without ``target_lang`` and the store writes it in: nothing the admin changed."""
        env = Env(self)
        series = env.translated(1, 2)
        old = hash_before_p12(TERM)
        for episode in (1, 2):
            version = env.store.current_version(env.output(episode)).version
            env.store.set_version(env.output(episode), version, glossary_hash=old)

        env.glossaries.write("series", "bazarr-7", "vi", TERM)

        self.assertIn("target_lang", env.store.glossary("series", "bazarr-7", "vi").doc)  # the store wrote it in
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        same = env.service.save_glossary(series, "vi", TERM, dry_run=True)
        self.assertEqual((same["stale_now"], same["would_stale"]), (0, 0))  # and the form agrees with what is stored


class ImpactTests(unittest.TestCase):
    """W1: whatever changes the rules says first, and without saving, how many translations it flags."""

    def test_a_style_counts_every_language_not_the_first_one(self):
        env = Env(self)
        series = env.translated(1)
        env.translate(1, job_id="fr-1", target_code="fr", target_language="French", output_path=env.output(1).replace(".vi.", ".fr."))
        self.assertEqual({o.target_lang for o in env.store.outputs(env.unit(1))}, {"vi", "fr"})

        for body in (
            {"style": {"tone": "wry"}, "dry_run": True},  # no lang: it used to count only the first output
            {"style": {"tone": "wry"}, "dry_run": True, "lang": "vi"},  # a lang does not narrow a style either
        ):
            with self.subTest(body=body):
                self.assertEqual(env.style.save_style(series, body)["would_stale"], 2)
        episode = env.style.save_unit_style(env.unit(1), {"style": {"tone": "frantic"}, "dry_run": True})
        self.assertEqual(episode["would_stale"], 2)
        card = env.style.save_card(series, {"card": {"name": "Kyber", "speech": "hums"}, "dry_run": True})
        self.assertEqual(card["would_stale"], 2)

        env.style.save_unit_style(env.unit(1), {"style": {"tone": "frantic"}})
        self.assertEqual(
            sorted((t["lang"], t["status"]) for t in env.library.unit_summary(env.store.get_unit(env.unit(1)))["targets"]),
            [("fr", "stale"), ("vi", "stale")],
        )

    def test_a_glossary_candidate_needs_its_language(self):
        env = Env(self)
        env.translated(1)
        owner = env.library.series_owner(env.series_id)
        with self.assertRaisesRegex(ValueError, "per language"):
            env.library.impact_units(owner.units, None, glossary=g.Glossary())

    def test_editing_a_preset_says_which_translations_it_flags_and_dry_run_changes_nothing(self):
        env = Env(self)
        series = env.translated(1, 2)
        env.style.save_style(series, {"preset": "drama", "style": {}})
        for episode in (1, 2):
            env.service.retranslate(env.unit(episode), {"lang": "vi"})
            env.run_pending_retranslation()
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        edit = {"name": "Drama", "genres": ["Drama"], "style": {"tone": "changed"}}

        preview = env.style.save_preset("drama", {**edit, "dry_run": True})
        self.assertEqual((preview["saved"], preview["would_stale"], preview["owners"]), (False, 2, 1))
        self.assertNotEqual(env.styles.preset("drama").style.tone, "changed")  # a dry run stores nothing
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})

        saved = env.style.save_preset("drama", edit)
        self.assertEqual((saved["saved"], saved["would_stale"], saved["id"]), (True, 2, "drama"))
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})
        again = env.style.save_preset("drama", {**edit, "dry_run": True})
        self.assertEqual((again["would_stale"], again["stale_now"]), (0, 2))  # already flagged: nothing new

    def test_a_preset_nobody_uses_flags_nothing(self):
        env = Env(self)
        env.translated(1)
        saved = env.style.save_preset("anime", {"name": "Anime", "genres": [], "style": {"tone": "bright"}})
        self.assertEqual((saved["would_stale"], saved["owners"]), (0, 0))
        self.assertEqual(env.statuses(), {1: "current"})

    def test_deleting_a_preset_says_what_the_series_that_named_it_lose(self):
        env = Env(self)
        series = env.translated(1, 2)
        env.style.clone_preset("sitcom", {"name": "Mine"})
        env.style.save_preset("mine", {"name": "Mine", "genres": [], "style": {"tone": "wry"}})
        env.style.save_style(series, {"preset": "mine", "style": {}})
        for episode in (1, 2):
            env.service.retranslate(env.unit(episode), {"lang": "vi"})
            env.run_pending_retranslation()
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})

        preview = env.style.delete_preset("mine", {"dry_run": True})
        self.assertEqual((preview["saved"], preview["would_stale"], preview["owners"]), (False, 2, 1))
        self.assertIsNotNone(env.styles.preset("mine"))
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})

        done = env.style.delete_preset("mine")
        self.assertEqual((done["saved"], done["would_stale"]), (True, 2))
        self.assertIsNone(env.styles.preset("mine"))
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})
        with self.assertRaisesRegex(ValueError, "built-in"):
            env.style.delete_preset("sitcom", {"dry_run": True})  # the refusal does not depend on dry_run
        with self.assertRaises(LookupError):
            env.style.delete_preset("nope", {"dry_run": True})

    def test_restoring_a_glossary_says_what_it_flags_before_it_does(self):
        env = Env(self)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Old, target: Cũ}\n")
        env.glossaries.write("series", "bazarr-7", "vi", TERM)
        series = env.translated(1, 2)
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        before = env.glossaries.stored("series", "bazarr-7", "vi").version

        preview = env.service.restore_glossary(series, "vi", 1, dry_run=True)
        self.assertEqual((preview["saved"], preview["would_stale"], preview["version"]), (False, 2, None))
        self.assertEqual(env.glossaries.stored("series", "bazarr-7", "vi").version, before)  # nothing was restored
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})

        done = env.service.restore_glossary(series, "vi", 1)
        self.assertEqual((done["saved"], done["would_stale"], done["restored"]), (True, 2, 1))
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})
        with self.assertRaises(LookupError):
            env.service.restore_glossary(series, "vi", 99, dry_run=True)

    def test_copying_a_glossary_says_what_it_flags_before_it_does(self):
        env = Env(self)
        series = env.translated(1, 2)
        env.store.upsert_series("bazarr-9", title="Other show")
        other = env.store.series_by_key("bazarr-9").id
        env.glossaries.write("series", "bazarr-9", "vi", "terms:\n  - {source: Other, target: Khác}\n")

        preview = env.service.copy_glossary(series, "vi", other, merge=True, dry_run=True)
        self.assertEqual((preview["saved"], preview["would_stale"], preview["from"]), (False, 2, "Other show"))
        self.assertIsNone(env.glossaries.stored("series", "bazarr-7", "vi"))
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})

        done = env.service.copy_glossary(series, "vi", other, merge=True)
        self.assertEqual((done["saved"], done["would_stale"]), (True, 2))
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})
        again = env.service.copy_glossary(series, "vi", other, merge=True, dry_run=True)
        self.assertEqual(again["would_stale"], 0)  # the same copy over a copy: nothing new

    def test_every_route_that_changes_the_rules_answers_with_an_impact(self):
        """One gate: a service method that can change a rules hash returns ``would_stale`` (and ``stale_now``)."""
        env = Env(self)
        series = env.translated(1, 2)
        first_unit = env.unit(1)  # looked up before the second series exists
        env.store.upsert_series("bazarr-9", title="Other show")
        other = env.store.series_by_key("bazarr-9").id
        env.glossaries.write("series", "bazarr-9", "vi", "terms:\n  - {source: Other, target: Khác}\n")
        env.glossaries.write("series", "bazarr-7", "vi", TERM)
        env.glossaries.write("series", "bazarr-7", "vi", TERM + "style: casual\n")
        calls = {
            "save_style": lambda: env.style.save_style(series, {"style": {"tone": "x"}, "dry_run": True}),
            "save_unit_style": lambda: env.style.save_unit_style(first_unit, {"style": {"tone": "x"}, "dry_run": True}),
            "save_card": lambda: env.style.save_card(series, {"card": {"name": "Zed", "speech": "x"}, "dry_run": True}),
            "delete_card": lambda: (
                env.style.save_card(series, {"card": {"name": "Zed", "speech": "x"}}),
                env.style.delete_card(series, "Zed"),
            )[1],
            "save_preset": lambda: env.style.save_preset(
                "drama", {"name": "Drama", "genres": [], "style": {"tone": "x"}, "dry_run": True}
            ),
            "delete_preset": lambda: (
                env.style.clone_preset("drama", {"name": "Mine"}), env.style.delete_preset("mine", {"dry_run": True}),
            )[1],
            "save_glossary": lambda: env.service.save_glossary(series, "vi", TERM, dry_run=True),
            "restore_glossary": lambda: env.service.restore_glossary(series, "vi", 1, dry_run=True),
            "copy_glossary": lambda: env.service.copy_glossary(series, "vi", other, merge=True, dry_run=True),
        }
        for name, call in calls.items():
            with self.subTest(name):
                answer = call()
                for key in ("would_stale", "stale_now", "stale_after"):
                    self.assertIn(key, answer)


class AdoptedVariantTests(unittest.TestCase):
    def adopt_variant(self, env, *, episode_style=None, apply_style=False):
        if episode_style:
            env.style.save_unit_style(env.unit(1), {"style": episode_style})
            env.service.retranslate(env.unit(1), {"lang": "vi"})
            env.run_pending_retranslation()
        env.service.variant(env.unit(1), {"lang": "vi", "style": {"tone": "dry"}})
        job = env.queued()[-1]
        env.jobs.jobs.transition(job["job_id"], "done", result="candidate")
        env.attempt.run(job, "", SETTINGS)
        version = next(v.version for v in env.store.versions(env.output(1)) if v.status == "candidate")
        return env.service.adopt(env.unit(1), version, "vi", apply_style=apply_style)

    def test_an_adopted_variant_is_not_stale_at_once_even_when_the_episode_has_its_own_style(self):
        env = Env(self)
        env.translated(1)
        self.adopt_variant(env, episode_style={"formality": "casual"})
        self.assertEqual(env.statuses(), {1: "current"})

    def test_an_adopted_variant_whose_style_is_kept_is_not_stale_at_once(self):
        env = Env(self)
        env.translated(1, 2)
        env.style.save_unit_style(env.unit(1), {"style": {"formality": "casual"}})
        env.service.retranslate(env.unit(1), {"lang": "vi"})
        env.run_pending_retranslation()
        answer = self.adopt_variant(env, apply_style=True)
        self.assertEqual(answer["would_stale"], 1)  # the other episode, as it always said
        self.assertEqual(env.statuses(), {1: "current", 2: "stale"})


class TrialRulesTests(unittest.TestCase):
    """R12-3: the trial of a style builds the rules a job builds, then numbers them for the sample window."""

    def test_the_window_renumbers_timed_rules_and_cue_edits_and_keeps_the_hash(self):
        env = Env(self)
        env.translated(1)
        unit = env.store.get_unit(env.unit(1))
        env.service.add_correction(unit.id, {
            "lang": "vi", "kind": "address",
            "payload": {"a": "Kyber", "b": "Zed", "a_to_b": "tôi–anh", "from_cue": 10},
        })
        env.service.add_correction(unit.id, {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 11, "text": "Sửa tay"}})
        cues = srt.parse_file(unit.source_path)
        rules = env.library.unit_rules(unit, "vi", cues=cues)
        self.assertEqual([(a.from_cue, a.until_cue) for a in rules.active_rules], [(10, None)])
        self.assertEqual(set(rules.locked), {11})

        window = [9, 10, 11, 12]
        moved = rules.for_window(window)

        self.assertEqual([(a.from_cue, a.until_cue) for a in moved.active_rules], [(2, None)])
        self.assertEqual(moved.locked, {3: "Sửa tay"})
        self.assertEqual((moved.hash, moved.style_hash), (rules.hash, rules.style_hash))
        self.assertEqual([(a.from_cue, a.until_cue) for a in rules.active_rules], [(10, None)])  # the original is not touched

        late = rules.for_window([1, 2, 3])  # a window the rule has not reached yet
        self.assertEqual(late.active_rules, [])
        self.assertEqual(late.locked, {})

    def test_a_rule_that_ended_before_the_window_is_left_out(self):
        env = Env(self)
        env.translated(1)
        unit = env.store.get_unit(env.unit(1))
        rules = env.library.unit_rules(unit, "vi", cues=srt.parse_file(unit.source_path))
        ended = replace(rules, active_rules=[g.ActiveRule(g.AddressRule(a="A", b="B", a_to_b="x"), 2, 5)])
        self.assertEqual(ended.for_window([7, 8]).active_rules, [])
        self.assertEqual([(a.from_cue, a.until_cue) for a in ended.for_window([3, 4, 5, 6]).active_rules], [(1, 3)])

    def test_the_trial_prompt_numbers_a_correction_by_the_window_it_sends(self):
        env = Env(self)
        series = env.translated(1)
        env.service.add_correction(env.unit(1), {
            "lang": "vi", "kind": "address",
            "payload": {"a": "Kyber", "b": "Zed", "a_to_b": "tôi–anh", "from_cue": 10},
        })
        env.fake.calls.clear()
        env.style.preview(series, {"lang": "vi", "unit_id": env.unit(1), "cues": [11]})
        system = env.fake.calls[0].system
        self.assertIn("Kyber speaking to Zed", system)
        self.assertIn("from item 2 on", system)  # cue 10 is the second line of the window 9-12
        self.assertNotIn("from item 10 on", system)

    def test_the_trial_still_works_for_an_episode_with_a_cue_edit_outside_the_window(self):
        env = Env(self)
        series = env.translated(1)
        env.service.add_correction(env.unit(1), {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 12, "text": "Sửa tay"}})
        result = env.style.preview(series, {"lang": "vi", "unit_id": env.unit(1), "cues": [2]})
        self.assertEqual([r["n"] for r in result["rows"]], [2])


if __name__ == "__main__":
    unittest.main()
