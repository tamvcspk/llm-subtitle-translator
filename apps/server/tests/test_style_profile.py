"""P12: style profiles, character cards, glossaries in the database, few-shot, the trial of a style and variants
(SPEC FR-18, criterion 16 a-g; ADR-14). Every provider is a fake; nothing touches the network."""

import json
import unittest
from pathlib import Path

from subcore import glossary as g
from subcore import srt
from subcore.style import Style

from subserver.api import style_api
from subserver.services.glossaries import Glossaries, IMPORTED_MARK
from subserver.jobs.queue import skip_reason
from subserver.services.style_service import StyleService, pick_samples
from subserver.services.styles import Styles
from subserver.services.versions import RETRANSLATE

from .test_corrections import SETTINGS, Env as BaseEnv, sha


class FakeMetadata:
    def __init__(self, genres=()):
        self._genres = tuple(genres)
        self.asked = []

    def description(self, job):
        return ""

    def genres(self, job):
        self.asked.append(job)
        return self._genres


class Env(BaseEnv):
    def __init__(self, test, genres=()):
        super().__init__(test)
        self.styles = Styles(self.store)
        self.styles.seed_presets()
        self.library.styles = self.styles
        self.metadata = FakeMetadata(genres)
        self.style = StyleService(
            self.library, self.styles, metadata=self.metadata, settings_source=lambda: SETTINGS,
            provider_factory=lambda settings, model, **kw: self.fake, usage=self.store,
        )
        self.series_id = None

    def translated(self, *episodes):
        for episode in episodes:
            self.translate(episode)
        self.series_id = self.store.list_series()[0].id
        return self.series_id

    def statuses(self):
        return {u["episode"]: u["status"] for u in self.library.series_units(self.series_id)["units"]}

    def last_system(self):
        return self.fake.calls[-1].system

    def queued(self):
        return self.jobs.jobs.by_state("queued")


class StyleLayersTests(unittest.TestCase):
    def test_16a_the_card_wins_over_the_series_in_the_prompt_and_a_locked_term_wins_over_both(self):
        env = Env(self)
        series = env.translated(1)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n")
        env.style.save_style(series, {"preset": "drama", "style": {"formality": "formal", "tone": "grave"}})
        env.style.save_card(series, {"card": {
            "name": "Kyber", "speech": "hums softly", "style": {"formality": "casual"},
        }})
        env.fake.calls.clear()
        env.translate(2)
        system = env.fake.calls[0].system

        self.assertIn("## 4. Effective style", system)  # after the glossary (3), before anything the machine knows
        self.assertIn("Formality: formal register", system)  # the series says formal ...
        self.assertIn("Tone: grave", system)  # ... over the drama preset's own tone
        self.assertIn("Character voices", system)
        voices = system.split("Character voices")[1]
        self.assertIn("Formality: casual, spoken register", voices)  # ... and the card, the top layer, says casual
        self.assertLess(system.index("Glossary"), system.index("Effective style"))
        self.assertIn("[LOCKED]", system)
        self.assertIn("tinh thể Kyber", env.cue_texts(2)[4])  # the locked term is in the output whatever the style says

    def test_a_card_is_in_the_prompt_only_where_the_dialogue_names_the_character(self):
        env = Env(self)
        series = env.translated(1)
        env.style.save_card(series, {"card": {"name": "Zed", "speech": "mumbles"}})
        env.style.save_card(series, {"card": {"name": "Kyber", "speech": "hums"}})
        env.fake.calls.clear()
        env.translate(2)
        system = env.fake.calls[0].system
        self.assertIn("hums", system)  # "Kyber" is named in cue 5
        self.assertNotIn("mumbles", system)  # nobody says Zed

    def test_the_language_layer_is_the_floor(self):
        env = Env(self)
        env.translated(1)
        self.assertIn("Dialect: neutral vocabulary", env.fake.calls[0].system)  # no style stored: the vi layer alone

    def test_the_style_of_one_episode_overrides_the_series(self):
        env = Env(self)
        series = env.translated(1, 2)
        env.style.save_style(series, {"style": {"tone": "dry"}})
        unit = env.unit(2)
        env.style.save_unit_style(unit, {"style": {"tone": "frantic"}})
        env.fake.calls.clear()
        env.translate(3)
        self.assertIn("Tone: dry", env.last_system())
        rules = env.library.unit_rules(env.store.get_unit(unit), "vi")
        self.assertEqual(rules.style.tone, "frantic")


class StaleTests(unittest.TestCase):
    def test_16b_a_style_change_flags_the_translations_and_creates_no_job(self):
        env = Env(self)
        series = env.translated(1, 2)
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        before = len(env.queued())

        preview = env.style.save_style(series, {"preset": "sitcom", "style": {"formality": "casual"}, "dry_run": True})
        self.assertEqual((preview["saved"], preview["would_stale"]), (False, 2))
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})  # a dry run changes nothing

        saved = env.style.save_style(series, {"preset": "sitcom", "style": {"formality": "casual"}})
        self.assertTrue(saved["saved"])
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})
        self.assertEqual(len(env.queued()), before)  # flagged, never redone by itself (FR-5.6)

    def test_a_style_nobody_set_leaves_every_hash_as_it_was_before_p12(self):
        """An episode with no stored style and no card keeps the hash it had before P12, so an upgrade flags nothing."""
        from subserver.domain.rules import StyleLayers, build_rules

        env = Env(self)
        env.translated(1)
        unit = env.store.get_unit(env.unit(1))
        self.assertEqual(env.library.style_layers(unit).fingerprint(), {})
        with_styles = env.library.unit_rules(unit, "vi").hash
        without = build_rules(g.Glossary(target_lang="vi"), season=unit.season, episode=unit.episode, lang="vi").hash
        self.assertEqual(with_styles, without)
        self.assertEqual(StyleLayers().fingerprint(), {})
        stored = env.store.current_version(env.output(1))
        self.assertEqual(stored.glossary_hash, with_styles)

    def test_a_card_change_flags_the_series_and_the_episode_style_only_its_own_episode(self):
        env = Env(self)
        series = env.translated(1, 2)
        env.style.save_card(series, {"card": {"name": "Kyber", "speech": "hums"}})
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})
        env.service.retranslate(env.unit(1), {"lang": "vi"})
        env.run_pending_retranslation()
        env.service.retranslate(env.unit(2), {"lang": "vi"})
        env.run_pending_retranslation()
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        env.style.save_unit_style(env.unit(2), {"style": {"tone": "frantic"}})
        self.assertEqual(env.statuses(), {1: "current", 2: "stale"})

    def test_the_style_of_one_episode_says_what_it_would_flag_before_it_saves(self):
        env = Env(self)
        env.translated(1, 2)
        preview = env.style.save_unit_style(env.unit(2), {"style": {"tone": "frantic"}, "dry_run": True})
        self.assertEqual((preview["saved"], preview["would_stale"], preview["style"]), (False, 1, {"tone": "frantic"}))
        self.assertEqual(env.style.unit_style(env.unit(2))["style"], {})  # a dry run stores nothing
        self.assertEqual(env.statuses(), {1: "current", 2: "current"})
        saved = env.style.save_unit_style(env.unit(2), {"style": {"tone": "frantic"}})
        self.assertEqual((saved["saved"], saved["would_stale"]), (True, 1))
        # The same override again flags nothing new: the episode is already stale.
        again = env.style.save_unit_style(env.unit(2), {"style": {"tone": "frantic"}, "dry_run": True})
        self.assertEqual(again["would_stale"], 0)

    def test_a_bad_style_is_refused_with_the_offending_field_and_stores_nothing(self):
        env = Env(self)
        series = env.translated(1)
        with self.assertRaisesRegex(ValueError, "formality"):
            env.style.save_style(series, {"style": {"formality": "rude"}})
        with self.assertRaises(LookupError):
            env.style.save_style(series, {"preset": "nope", "style": {}})
        self.assertEqual(env.style.overview(series)["style"], {})


class FewShotTests(unittest.TestCase):
    def edit(self, env, episode, cue, text):
        env.service.add_correction(env.unit(episode), {
            "lang": "vi", "kind": "cue_edit", "payload": {"cue": cue, "text": text},
        })

    def test_16e_only_the_edited_cues_of_the_same_series_inside_the_budget(self):
        env = Env(self)
        env.translated(1)
        self.edit(env, 1, 5, "Tinh thể Kyber sáng lên rồi")  # "The Kyber crystal glows"
        # another series, with an edit of a line that looks just as close
        other = env.job(1, series_id="9", job_id="other-1")
        other["subtitle_path"] = str(env.media / "Other.S01E01.en.srt")
        Path(other["subtitle_path"]).write_text((env.media / "Show.S01E01.en.srt").read_text(encoding="utf-8"), encoding="utf-8")
        other["video_path"] = str(env.media / "Other.S01E01.mkv")
        other["output_path"] = str(env.media / "Other.S01E01.vi.srt")
        env.attempt.run(other, "", SETTINGS)
        other_unit = env.store.list_units(env.store.series_by_key("bazarr-9").id)[0].id
        env.service.add_correction(other_unit, {
            "lang": "vi", "kind": "cue_edit", "payload": {"cue": 5, "text": "Của series khác"},
        })

        env.fake.calls.clear()
        env.translate(2)
        system = env.fake.calls[0].system
        self.assertIn("Accepted translations of other lines", system)
        self.assertIn("Tinh thể Kyber sáng lên rồi", system)
        self.assertNotIn("Của series khác", system)  # another series' edit is never an example

        # the budget bounds the section, and 0 turns it off
        for episode, budget, expected in ((3, 1, False), (4, 0, False), (5, 600, True)):
            with self.subTest(budget=budget):
                env.fake.calls.clear()
                env.attempt.run(env.job(episode, job_id=f"budget-{budget}"), "", {**SETTINGS, "few_shot_budget_tokens": budget})
                self.assertEqual("Tinh thể Kyber sáng lên rồi" in env.fake.calls[0].system, expected)

    def test_a_units_own_edits_are_kept_not_shown_as_examples(self):
        env = Env(self)
        env.translated(1)
        self.edit(env, 1, 5, "Tinh thể Kyber sáng lên rồi")
        env.service.retranslate(env.unit(1), {"lang": "vi"})
        env.fake.calls.clear()
        env.run_pending_retranslation()
        self.assertNotIn("Accepted translations of other lines", env.fake.calls[0].system)  # the edit is `keep`, not an example

    def test_the_boundary_a_cue_edit_in_episode_one_changes_no_stale_flag(self):
        env = Env(self)
        env.translated(1, 2, 3)
        before = env.statuses()
        hashes = {n: env.library.unit_rules(env.store.get_unit(env.unit(n)), "vi").hash for n in (1, 2, 3)}
        self.edit(env, 1, 5, "Tinh thể Kyber sáng lên rồi")
        self.assertEqual(env.statuses(), before)
        self.assertEqual({n: env.library.unit_rules(env.store.get_unit(env.unit(n)), "vi").hash for n in (1, 2, 3)}, hashes)
        # ... yet the edit reaches the next episode's bundle
        env.fake.calls.clear()
        env.translate(4)
        self.assertIn("Tinh thể Kyber sáng lên rồi", env.fake.calls[0].system)


class RangeStampTests(unittest.TestCase):
    def test_a_retranslated_range_stamps_the_whole_episode_with_the_current_rules_and_says_the_range(self):
        env = Env(self)
        series = env.translated(1, 2)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Line, target: Dòng, locked: false}\n")
        self.assertEqual(env.statuses(), {1: "stale", 2: "stale"})

        env.service.retranslate(env.unit(1), {"lang": "vi", "range": {"cues": [3, 5]}})
        env.run_pending_retranslation()

        self.assertEqual(env.statuses(), {1: "current", 2: "stale"})  # episode 1 no longer stale, episode 2 still is
        current = env.store.current_version(env.output(1))
        self.assertEqual(current.params, {"range": {"first": 3, "last": 5}})
        self.assertEqual(current.trigger, RETRANSLATE)
        self.assertEqual(
            current.glossary_hash, env.library.unit_rules(env.store.get_unit(env.unit(1)), "vi").hash,
        )


class TrialTests(unittest.TestCase):
    def test_pick_samples_spreads_about_twenty_over_the_episode(self):
        texts = [f"this is line number {n}" for n in range(300)]
        picked = pick_samples(texts, 20)
        self.assertEqual(len(picked), 20)
        self.assertEqual(picked, sorted(picked))
        self.assertLess(picked[0], 20)
        self.assertGreater(picked[-1], 280)
        self.assertEqual(pick_samples(["a b c d", "", "e f g h"], 20), [1, 3])  # empty cues are never samples
        self.assertEqual(len(pick_samples(["a b c d"] * 5, 3)), 3)
        # one-word interjections are skipped while enough longer lines exist, and used when there are not
        longer = [f"a longer line {n}" for n in range(6)]
        self.assertEqual(pick_samples(["ha", *longer, "ok"], 3), [2, 4, 6])
        self.assertEqual(pick_samples(["ha", "ok", "no way, really"], 2), [1, 2])

    def test_16c_the_trial_calls_the_provider_for_the_samples_and_writes_nothing(self):
        env = Env(self)
        series = env.translated(1)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n")
        target = env.output(1)
        stored = sha(target)
        versions = [(v.version, v.status) for v in env.store.versions(target)]
        files = sorted(str(p) for p in env.root.rglob("*") if p.is_file() and "app.db" not in p.name)
        env.fake.calls.clear()

        result = env.style.preview(series, {"lang": "vi", "unit_id": env.unit(1), "count": 4, "style": {"tone": "frantic"}})

        self.assertEqual(len(env.fake.calls), 1)  # one provider call for the samples
        self.assertEqual(len(result["rows"]), 4)
        self.assertIn("Tone: frantic", env.fake.calls[0].system)  # the draft style, not the stored one
        row = result["rows"][0]
        self.assertTrue(row["preview"].startswith("[vi] "))
        self.assertEqual(row["current"], env.cue_texts(1)[row["n"] - 1])  # next to what the episode has now
        self.assertEqual(sha(target), stored)
        self.assertEqual([(v.version, v.status) for v in env.store.versions(target)], versions)  # no version
        self.assertEqual(sorted(str(p) for p in env.root.rglob("*") if p.is_file() and "app.db" not in p.name), files)
        self.assertEqual(env.style.overview(series)["style"], {})  # the draft was not saved
        self.assertEqual(result["model"], "m-1")

    def test_the_trial_can_take_hand_picked_cues_and_refuses_bad_ones(self):
        env = Env(self)
        series = env.translated(1)
        result = env.style.preview(series, {"lang": "vi", "unit_id": env.unit(1), "cues": [2, 5]})
        self.assertEqual([r["n"] for r in result["rows"]], [2, 5])
        for body in ({"cues": [0]}, {"cues": [99]}, {"cues": "x"}, {"cues": list(range(1, 50))}, {"count": "many"}):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    env.style.preview(series, {"unit_id": env.unit(1), **body})

    def test_the_trial_keeps_the_glossary_authoritative_and_sends_no_timecodes(self):
        env = Env(self)
        series = env.translated(1)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n")
        env.fake.calls.clear()
        env.style.preview(series, {"unit_id": env.unit(1), "cues": [5]})
        sent = env.fake.calls[0].system + env.fake.calls[0].user
        self.assertIn("[LOCKED]", sent)
        self.assertNotIn("-->", sent)

    def test_a_preview_without_a_source_subtitle_to_try_on_says_so(self):
        env = Env(self)
        series = env.translated(1)
        Path(env.store.get_unit(env.unit(1)).source_path).unlink()
        with self.assertRaises(LookupError):
            env.style.preview(series, {"unit_id": env.unit(1)})


class VariantTests(unittest.TestCase):
    def run_variant(self, env, unit, **body):
        env.service.variant(unit, {"lang": "vi", **body})
        job = env.queued()[-1]
        env.jobs.jobs.transition(job["job_id"], "done", result="candidate")  # it leaves the line
        return job, env.attempt.run(job, "", SETTINGS)

    def test_16d_a_variant_job_makes_a_candidate_and_never_touches_the_target(self):
        env = Env(self)
        env.translated(1)
        target = env.output(1)
        stored = sha(target)
        before = [v.version for v in env.store.versions(target)]

        env.service.variant(env.unit(1), {"lang": "vi", "label": "casual", "style": {"formality": "casual"}})
        job = env.queued()[-1]
        self.assertEqual((job["mode"], job["trigger"], job["origin"]), ("variant", "variant", "variant"))
        self.assertIsNone(skip_reason(job))  # an existing target is no reason to skip a variant
        env.jobs.jobs.transition(job["job_id"], "done")
        env.fake.calls.clear()
        status = env.attempt.run(job, "", SETTINGS)

        self.assertEqual(status, "candidate")
        self.assertEqual(sha(target), stored)  # the file on disk is as it was
        self.assertEqual(env.store.current_version(target).version, before[-1])
        candidate = [v for v in env.store.versions(target) if v.status == "candidate"]
        self.assertEqual(len(candidate), 1)
        self.assertEqual(candidate[0].trigger, "variant")
        self.assertEqual(candidate[0].params["variant"]["label"], "casual")
        self.assertEqual(candidate[0].params["variant"]["style"], {"formality": "casual"})
        self.assertIn("Formality: casual, spoken register", env.fake.calls[0].system)  # the variant's style, not the series'

    def test_a_variant_does_not_become_the_cache_of_a_normal_translation(self):
        env = Env(self)
        env.translated(1)
        self.run_variant(env, env.unit(1), style={"tone": "dry"})
        self.assertEqual(env.statuses(), {1: "current"})  # nothing about the episode's own state changed

    def test_only_one_variant_is_open_per_episode_and_a_bad_style_is_refused_up_front(self):
        env = Env(self)
        env.translated(1)
        env.service.variant(env.unit(1), {"lang": "vi", "style": {"tone": "dry"}})
        with self.assertRaisesRegex(ValueError, "already queued"):
            env.service.variant(env.unit(1), {"lang": "vi", "style": {"tone": "wry"}})
        env2 = Env(self)
        env2.translated(1)
        with self.assertRaisesRegex(ValueError, "max_lines"):
            env2.service.variant(env2.unit(1), {"lang": "vi", "style": {"max_lines": 9}})
        with self.assertRaises(LookupError):
            env2.service.variant(env2.unit(1), {"lang": "vi", "preset": "nope"})
        self.assertEqual(env2.queued(), [])

    def test_the_comparison_lists_both_texts_and_a_verdict_is_a_quality_signal(self):
        env = Env(self)
        env.translated(1)
        env.prefix = "[alt] "
        self.run_variant(env, env.unit(1), label="alt", style={"tone": "dry"})
        version = next(v.version for v in env.store.versions(env.output(1)) if v.status == "candidate")

        view = env.service.compare(env.unit(1), version, "vi")
        self.assertEqual((view["differing"], view["aligned"]), (12, True))
        self.assertTrue(view["rows"][0]["current"].startswith("[vi] "))
        self.assertTrue(view["rows"][0]["variant"].startswith("[alt] "))

        signals = []
        env.service.telemetry.signal = lambda kind, **data: signals.append((kind, data))
        done = env.service.choose(env.unit(1), version, {"lang": "vi", "picks": {"2": "variant", "3": "current"}})
        self.assertEqual(done["recorded"], 2)
        self.assertEqual([(k, d["cue"], d["winner"]) for k, d in signals], [("ab", 2, "variant"), ("ab", 3, "current")])
        self.assertEqual(signals[0][1]["label"], "alt")
        self.assertEqual(signals[0][1]["style"], {"tone": "dry"})
        self.assertTrue(signals[0][1]["prompt_version"])  # scores add up by style and prompt version (FR-19.5)
        self.assertEqual(env.cue_texts(1)[1], "[vi] Line 2 of episode 1")  # nothing was applied

    def test_applying_a_verdict_turns_the_chosen_cues_into_edits_and_a_whole_win_adopts_the_candidate(self):
        env = Env(self)
        env.translated(1)
        env.prefix = "[alt] "
        self.run_variant(env, env.unit(1), style={"tone": "dry"})
        version = next(v.version for v in env.store.versions(env.output(1)) if v.status == "candidate")

        done = env.service.choose(env.unit(1), version, {"lang": "vi", "picks": {"2": "variant"}, "apply": True})
        self.assertEqual(done["applied"]["edited_cues"], [2])
        self.assertEqual(env.cue_texts(1)[1], "[alt] Line 2 of episode 1")
        self.assertEqual(env.cue_texts(1)[2], "[vi] Line 3 of episode 1")

        env.service.choose(env.unit(1), version, {"lang": "vi", "whole": "variant", "apply": True})
        self.assertEqual(env.cue_texts(1)[2], "[alt] Line 3 of episode 1")  # "Use this version"
        with self.assertRaisesRegex(ValueError, "not a candidate"):
            env.service.choose(env.unit(1), version, {"lang": "vi", "whole": "variant"})

    def test_a_verdict_names_its_bad_input(self):
        env = Env(self)
        env.translated(1)
        self.run_variant(env, env.unit(1), style={"tone": "dry"})
        version = next(v.version for v in env.store.versions(env.output(1)) if v.status == "candidate")
        for body in ({"picks": {"x": "variant"}}, {"picks": {"99": "variant"}}, {"picks": {"2": "maybe"}}, {"whole": "x"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                env.service.choose(env.unit(1), version, {"lang": "vi", **body})


class PresetTests(unittest.TestCase):
    def test_16f_presets_are_suggested_from_the_tmdb_genres_through_the_metadata_port(self):
        env = Env(self, genres=("Animation", "Drama"))
        series = env.translated(1)
        found = env.style.suggest(series)
        self.assertEqual(found["genres"], ["Animation", "Drama"])
        self.assertEqual([p["id"] for p in found["presets"]], ["anime", "drama"])
        self.assertEqual(env.metadata.asked, [{"media_type": "series", "series_id": "7"}])  # asked on demand, with the Bazarr id

    def test_no_genres_no_suggestion(self):
        env = Env(self, genres=())
        series = env.translated(1)
        self.assertEqual(env.style.suggest(series), {"genres": [], "presets": []})

    def test_a_preset_is_cloned_edited_and_only_a_custom_one_can_be_deleted(self):
        env = Env(self)
        clone = env.style.clone_preset("sitcom", {"name": "My Sitcom"})
        self.assertEqual(clone["id"], "my-sitcom")
        self.assertFalse(clone["builtin"])
        env.style.save_preset("my-sitcom", {"name": "My Sitcom", "genres": ["Comedy"], "style": {"tone": "wry"}})
        self.assertEqual(env.styles.preset("my-sitcom").style.tone, "wry")
        with self.assertRaisesRegex(ValueError, "built-in"):
            env.style.delete_preset("sitcom")
        deleted = env.style.delete_preset("my-sitcom")
        self.assertEqual((deleted["ok"], deleted["saved"], deleted["would_stale"], deleted["owners"]), (True, True, 0, 0))
        with self.assertRaises(ValueError):
            env.style.clone_preset("sitcom", {"name": "Sitcom"})  # the name is taken

    def test_an_edited_preset_survives_a_restart_and_seeding_only_adds_what_is_missing(self):
        env = Env(self)
        env.style.save_preset("drama", {"name": "Drama", "genres": ["Drama"], "style": {"tone": "mine"}})
        self.assertEqual(env.styles.seed_presets(), 0)
        self.assertEqual(env.styles.preset("drama").style.tone, "mine")
        env.store.style_delete("preset", "kids")
        self.assertEqual(env.styles.seed_presets(), 1)

    def test_a_preset_edit_flags_the_series_that_use_it(self):
        env = Env(self)
        series = env.translated(1)
        env.style.save_style(series, {"preset": "drama", "style": {}})
        env.service.retranslate(env.unit(1), {"lang": "vi"})
        env.run_pending_retranslation()
        self.assertEqual(env.statuses(), {1: "current"})
        env.style.save_preset("drama", {"name": "Drama", "genres": ["Drama"], "style": {"tone": "changed"}})
        self.assertEqual(env.statuses(), {1: "stale"})


class GlossaryInDatabaseTests(unittest.TestCase):
    YAML = (
        "version: 1\ntarget_lang: vi\nstyle: legacy note\n"
        "terms:\n  - {source: Kyber crystal, target: tinh thể Kyber, aliases: [Kyber crystals], locked: true, from: S01E03}\n"
        "characters:\n  - {name: Alice, gender: f}\n"
        "address_rules:\n  - {a: Alice, b: Bob, a_to_b: 'em–anh', from: 'S01E06 00:32:10', until: S02E01, note: after}\n"
    )

    def test_16g_yaml_in_and_out_through_the_database_is_the_same_glossary(self):
        env = Env(self)
        env.glossaries.write("series", "bazarr-7", "vi", self.YAML)
        exported = env.glossaries.read_text("series", "bazarr-7", "vi")
        self.assertEqual(g.loads(exported), g.loads(self.YAML))
        env.glossaries.write("series", "bazarr-7", "pt-br", exported)  # and again, from the export
        self.assertEqual(env.glossaries.load("series", "bazarr-7", "pt-br").terms, g.loads(self.YAML).terms)
        self.assertEqual(env.glossaries.languages("series", "bazarr-7"), ["pt-br", "vi"])

    def test_saving_the_same_glossary_again_is_not_a_new_version(self):
        env = Env(self)
        first = env.glossaries.write("series", "bazarr-7", "vi", self.YAML)
        again = env.glossaries.write("series", "bazarr-7", "vi", env.glossaries.read_text("series", "bazarr-7", "vi"))
        self.assertEqual((first.version, again.version), (1, 1))
        self.assertEqual(env.glossaries.history("series", "bazarr-7", "vi"), [])

    def test_history_keeps_what_each_save_replaced_and_a_restore_is_itself_undoable(self):
        env = Env(self)
        glossaries = env.glossaries
        glossaries.write("series", "s", "vi", "terms:\n  - {source: A, target: B}\n")
        glossaries.write("series", "s", "vi", "terms:\n  - {source: C, target: D}\n")
        glossaries.write("series", "s", "vi", "terms:\n  - {source: E, target: F}\n")
        history = glossaries.history("series", "s", "vi")
        self.assertEqual([(h.version, h.note) for h in history], [(2, "import"), (1, "import")])
        glossaries.restore("series", "s", "vi", 1)
        self.assertEqual(glossaries.load("series", "s", "vi").terms[0].source, "A")
        self.assertEqual(glossaries.stored("series", "s", "vi").version, 4)
        self.assertEqual(glossaries.history("series", "s", "vi")[0].doc["terms"][0]["source"], "E")  # the restore can be undone
        with self.assertRaises(LookupError):
            glossaries.restore("series", "s", "vi", 99)

    def test_the_old_yaml_files_are_imported_once_and_left_where_they_are(self):
        env = Env(self)
        series = env.data / "series" / "bazarr-7"
        series.mkdir(parents=True)
        (series / "glossary.vi.yaml").write_text(self.YAML, encoding="utf-8")
        (series / "glossary.pt-br.yaml").write_text("terms: [broken\n", encoding="utf-8")
        movie = env.data / "movies" / "tmdb-9"
        movie.mkdir(parents=True)
        (movie / "glossary.vi.yaml").write_text("terms:\n  - {source: A, target: B}\n", encoding="utf-8")
        before = {p: p.read_bytes() for p in env.data.rglob("glossary.*.yaml")}

        with self.assertLogs(level="WARNING") as logged:
            report = env.glossaries.import_files(env.data)

        self.assertIn("was not imported", "\n".join(logged.output))
        self.assertEqual((report.imported, report.skipped), (2, 0))
        self.assertEqual(len(report.broken), 1)
        self.assertTrue(report.broken[0].endswith("glossary.pt-br.yaml"))
        self.assertEqual(env.glossaries.load("series", "bazarr-7", "vi"), g.loads(self.YAML))
        self.assertEqual(env.glossaries.load("movies", "tmdb-9", "vi").terms[0].target, "B")
        self.assertIsNone(env.glossaries.stored("series", "bazarr-7", "pt-br"))
        self.assertEqual({p: p.read_bytes() for p in env.data.rglob("glossary.*.yaml")}, before)  # the files stay

        again = env.glossaries.import_files(env.data)  # idempotent: the second run reads nothing
        self.assertEqual((again.imported, again.skipped, again.broken), (0, 0, ()))
        self.assertEqual(env.glossaries.history("series", "bazarr-7", "vi"), [])
        self.assertTrue(any(r.key == IMPORTED_MARK for r in env.store.settings_rows()))

    def test_a_glossary_already_in_the_database_wins_over_its_file(self):
        env = Env(self)
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Mine, target: Của tôi}\n")
        folder = env.data / "series" / "bazarr-7"
        folder.mkdir(parents=True)
        (folder / "glossary.vi.yaml").write_text(self.YAML, encoding="utf-8")
        report = env.glossaries.import_files(env.data)
        self.assertEqual((report.imported, report.skipped), (0, 1))
        self.assertEqual(env.glossaries.load("series", "bazarr-7", "vi").terms[0].source, "Mine")

    def test_a_glossary_is_copied_from_another_series_not_linked(self):
        env = Env(self)
        env.translated(1)
        env.glossaries.write("series", "bazarr-9", "vi", "terms:\n  - {source: Other, target: Khác}\n")
        env.store.upsert_series("bazarr-9", title="Other show")
        other = env.store.series_by_key("bazarr-9").id
        series = env.store.series_by_key("bazarr-7").id
        env.glossaries.write("series", "bazarr-7", "vi", "terms:\n  - {source: Mine, target: Của tôi}\n")

        merged = env.service.copy_glossary(series, "vi", other, merge=True)
        self.assertEqual(merged["from"], "Other show")
        self.assertEqual({t.source for t in env.glossaries.load("series", "bazarr-7", "vi").terms}, {"Mine", "Other"})
        env.service.copy_glossary(series, "vi", other, merge=False)
        self.assertEqual({t.source for t in env.glossaries.load("series", "bazarr-7", "vi").terms}, {"Other"})
        env.glossaries.write("series", "bazarr-9", "vi", "terms:\n  - {source: Changed, target: Đổi}\n")
        self.assertEqual({t.source for t in env.glossaries.load("series", "bazarr-7", "vi").terms}, {"Other"})  # no link
        with self.assertRaisesRegex(ValueError, "another series"):
            env.service.copy_glossary(series, "vi", series, merge=True)
        env.store.upsert_series("bazarr-10", title="Empty")
        with self.assertRaises(LookupError):
            env.service.copy_glossary(series, "vi", env.store.series_by_key("bazarr-10").id, merge=True)

    def test_the_form_saves_a_document_and_a_bad_document_names_its_path(self):
        env = Env(self)
        series = env.translated(1)
        saved = env.service.save_glossary(series, "vi", doc={"terms": [{"source": "A", "target": "B", "locked": True}]})
        self.assertEqual(saved["version"], 1)
        shown = env.service.glossary(series, "vi")
        self.assertEqual(shown["doc"]["terms"][0]["source"], "A")
        self.assertIn("source: A", shown["yaml"])
        with self.assertRaisesRegex(ValueError, r"terms\[0\]"):
            env.service.save_glossary(series, "vi", doc={"terms": [{"source": "A"}]})
        self.assertEqual(env.service.glossary(series, "vi")["version"], 1)


class FilteredGlossaryTests(unittest.TestCase):
    def test_a_big_glossary_lists_only_the_terms_a_batch_uses_but_pronoun_rules_stay(self):
        env = Env(self)
        terms = "".join(f"  - {{source: Word{n:03d}x, target: Từ{n}}}\n" for n in range(60))
        env.glossaries.write(
            "series", "bazarr-7", "vi",
            "terms:\n" + terms + "  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}\n"
            "address_rules:\n  - {a: Kyber, b: Zed, a_to_b: 'tôi–anh'}\n",
        )
        env.translate(2)
        system = env.fake.calls[0].system
        self.assertIn('"Kyber crystal"', system)  # cue 5 mentions it
        self.assertNotIn("Word000x", system)  # 61 terms is over 50: unused ones are left out
        self.assertIn("Kyber speaking to Zed", system)  # address rules are never filtered by words

        env.fake.calls.clear()
        env.attempt.run(env.job(3, job_id="small"), "", {**SETTINGS, "glossary_filter_threshold": 0})
        self.assertIn("Word000x", env.fake.calls[0].system)  # 0 = never filter


class RoutingTests(unittest.TestCase):
    def call(self, env, method, path, query=None, body=None):
        return style_api.handle(env.style, method, path, query or {}, body)

    def test_the_routes_map_lookup_and_value_errors_to_404_and_400(self):
        env = Env(self)
        series = env.translated(1)
        status, payload = self.call(env, "GET", f"/api/series/{series}/style", {"lang": ["vi"]})
        self.assertEqual((status, payload["preset"], len(payload["presets"])), (200, "", 5))
        status, payload = self.call(env, "PUT", f"/api/series/{series}/style", {}, {"preset": "drama", "style": {"tone": "x"}})
        self.assertEqual((status, payload["saved"]), (200, True))
        self.assertEqual(self.call(env, "PUT", f"/api/series/{series}/style", {}, {"style": {"formality": "rude"}})[0], 400)
        self.assertEqual(self.call(env, "GET", "/api/series/999/style")[0], 404)
        self.assertEqual(self.call(env, "PUT", f"/api/series/{series}/style", {}, {"preset": "nope"})[0], 404)
        status, payload = self.call(env, "PUT", f"/api/series/{series}/cards", {}, {"card": {"name": "Bob", "speech": "slow"}})
        self.assertEqual((status, payload["card"]["name"]), (200, "Bob"))
        self.assertEqual(self.call(env, "PUT", f"/api/series/{series}/cards", {}, {"card": {"name": ""}})[0], 400)
        self.assertEqual(self.call(env, "GET", f"/api/series/{series}/style")[1]["cards"][0]["name"], "Bob")
        self.assertEqual(self.call(env, "POST", f"/api/series/{series}/cards/delete", {}, {"name": "Bob"})[0], 200)
        self.assertEqual(self.call(env, "POST", f"/api/series/{series}/cards/delete", {}, {"name": "Bob"})[0], 404)
        self.assertEqual(self.call(env, "GET", "/api/style/presets")[1]["items"][0]["id"], "anime")
        self.assertEqual(self.call(env, "PUT", "/api/style/presets/nope", {}, {})[0], 404)
        self.assertIsNone(self.call(env, "GET", "/api/queue"))
        self.assertIsNone(self.call(env, "DELETE", f"/api/series/{series}/style"))
        status, payload = self.call(env, "PUT", f"/api/units/{env.unit(1)}/style", {}, {"style": {"tone": "frantic"}})
        self.assertEqual((status, payload["style"]), (200, {"tone": "frantic"}))
        self.assertEqual(self.call(env, "GET", f"/api/units/{env.unit(1)}/style")[1], {"style": {"tone": "frantic"}})
        self.assertEqual(self.call(env, "POST", f"/api/series/{series}/style/preview", {}, {"cues": ["a"]})[0], 400)

    def test_style_paths_are_claimed_by_the_style_router_only(self):
        for path in ("/api/series/3/style", "/api/series/3/cards", "/api/style/presets", "/api/series/3/style/preview"):
            self.assertTrue(style_api.handles(path), path)
        for path in ("/api/series/3/glossary", "/api/queue", "/api/units/3/cues"):
            self.assertFalse(style_api.handles(path), path)


class HttpWiringTests(unittest.TestCase):
    """The real handler serves the style routes behind the same authentication as the others."""

    def test_the_style_routes_are_served_over_http_and_need_credentials(self):
        import base64
        import http.client
        import threading
        from http.server import ThreadingHTTPServer

        import worker
        from subserver.services.auth import ConsoleAuth

        env = Env(self)
        series = env.translated(1)
        server = ThreadingHTTPServer(("127.0.0.1", 0), worker.ConsoleHandler)
        server.ctx = {"units": env.service, "style": env.style, "static_dir": str(env.root), "queue_dir": env.queue_dir}
        server.auth = ConsoleAuth.from_env({"CONSOLE_USER": "u", "CONSOLE_PASSWORD": "p"})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

        def request(method, path, body=None, headers=auth):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            try:
                conn.request(method, path, body=None if body is None else json.dumps(body), headers=dict(headers))
                response = conn.getresponse()
                return response.status, json.loads(response.read().decode("utf-8"))
            finally:
                conn.close()

        self.assertEqual(request("GET", f"/api/series/{series}/style", headers={})[0], 401)
        self.assertEqual(request("PUT", f"/api/series/{series}/style", {"style": {}}, headers={})[0], 401)
        status, body = request("GET", f"/api/series/{series}/style?lang=vi")
        self.assertEqual((status, len(body["presets"])), (200, 5))
        status, body = request("PUT", f"/api/series/{series}/style?lang=vi", {"preset": "kids", "style": {"tone": "warm"}})
        self.assertEqual((status, body["saved"], body["would_stale"]), (200, True, 1))
        status, body = request("PUT", f"/api/series/{series}/style?lang=vi", {"style": {"max_lines": 9}})
        self.assertEqual(status, 400)
        self.assertIn("max_lines", body["error"])
        status, body = request("PUT", f"/api/series/{series}/cards?lang=vi", {"card": {"name": "Bob", "speech": "slow"}})
        self.assertEqual((status, body["card"]["name"]), (200, "Bob"))
        status, body = request("POST", f"/api/series/{series}/style/preview", {"unit_id": env.unit(1), "cues": [2]})
        self.assertEqual((status, [r["n"] for r in body["rows"]]), (200, [2]))
        status, body = request("POST", f"/api/units/{env.unit(1)}/variant", {"lang": "vi", "label": "dry", "style": {"tone": "dry"}})
        self.assertEqual((status, body["label"]), (200, "dry"))
        self.assertEqual(request("POST", f"/api/units/{env.unit(1)}/variant", {"lang": "vi", "style": {}})[0], 400)  # already queued
        self.assertEqual(request("GET", f"/api/series/{series}/glossary/history?lang=vi")[1], {"lang": "vi", "items": []})
        self.assertEqual(request("GET", "/api/style/presets")[0], 200)
        self.assertEqual(request("GET", "/api/series/999/style")[0], 404)


if __name__ == "__main__":
    unittest.main()
