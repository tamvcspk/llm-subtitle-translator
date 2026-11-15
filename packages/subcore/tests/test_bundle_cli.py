"""The declarative CLI works from bundles: same prompt and output as the engine, flags over bundles, key order,
validate and compare (FR-21.3, FR-21.4; criteria 18a, 18b, 18e, 18f, 18g)."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subcore import cli, srt
from subcore.bundle import TranslationBundle
from subcore.glossary import loads as load_glossary
from subcore.providers import FakeProvider
from subcore.translate import PromptContext
from subcore.translate.engine import make_bundle, render_requests, run
from subcore.translate.review import review

from .helpers import make_cues, numbered

GLOSSARY_YAML = """\
version: 1
terms:
  - {source: Kyber crystal, target: tinh thể Kyber, locked: true}
"""


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cues = numbered(5, "Kyber crystal {n} glows")
        self.src = self.dir / "Movie.en.srt"
        self.src.write_text(srt.serialize(self.cues), encoding="utf-8")
        self.glossary = self.dir / "g.yaml"
        self.glossary.write_text(GLOSSARY_YAML, encoding="utf-8")

    def path(self, name):
        return str(self.dir / name)


class TranslateFromBundleTests(CliTestBase):
    def test_translate_with_bundle_equals_engine_output_byte_for_byte(self):
        """Criterion 18a: the bundle written by the CLI, run again by the CLI, matches an in-process run."""
        b_path = self.path("b.json")
        code, _, err = run_cli("bundle", str(self.src), "--glossary", str(self.glossary), "--title", "Show",
                               "--season", "1", "--episode", "2", "-o", b_path)
        self.assertEqual(code, 0, err)
        code, _, err = run_cli("translate", str(self.src), "--bundle", b_path, "--provider", "fake",
                               "--out", self.path("cli.vi.srt"))
        self.assertEqual(code, 0, err)

        bundle = TranslationBundle.from_json(Path(b_path).read_text(encoding="utf-8"))
        engine = run(bundle, FakeProvider(prefix="[vi] "))
        cli_text = Path(self.path("cli.vi.srt")).read_text(encoding="utf-8")
        self.assertEqual(cli_text, srt.serialize(engine.cues))

    def test_worker_and_cli_send_the_same_prompt(self):
        """Criterion 18a: the requests a worker-style run sends are the ones render-prompt prints."""
        ctx = PromptContext(target_lang="vi", glossary=load_glossary(GLOSSARY_YAML), title="Show", season=1, episode=2)
        bundle = make_bundle(self.cues, ctx, model="fake-1", max_output_tokens=4000)
        worker = FakeProvider()
        run(bundle, worker)
        code, out, err = run_cli("render-prompt", str(self.src), "--glossary", str(self.glossary), "--title", "Show",
                                 "--season", "1", "--episode", "2", "--max-output-tokens", "4000")
        self.assertEqual(code, 0, err)
        first = worker.calls[0]
        self.assertIn(first.system, out)
        self.assertIn(first.user, out)
        self.assertEqual(render_requests(bundle)[0].user, first.user)

    def test_render_prompt_never_builds_a_provider(self):
        """Criterion 18b: no key, no provider, no call."""
        with mock.patch.object(cli, "_build_provider", side_effect=AssertionError("provider built")):
            code, out, err = run_cli("render-prompt", str(self.src), "--glossary", str(self.glossary))
        self.assertEqual(code, 0, err)
        self.assertIn("----- system", out)

    def test_flags_win_over_the_bundle(self):
        """FR-21.3: a flag given with a bundle replaces that value; the rest of the bundle stays."""
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "--target", "vi", "-o", b_path)
        code, _, err = run_cli("translate", str(self.src), "--bundle", b_path, "--target", "fr",
                               "--provider", "fake", "--out", self.path("fr.srt"))
        self.assertEqual(code, 0, err)
        self.assertTrue(Path(self.path("fr.srt")).read_text(encoding="utf-8").count("[fr] ") == 5)

    def test_bundle_for_another_file_is_refused(self):
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "-o", b_path)
        other = self.dir / "Other.en.srt"
        other.write_text(srt.serialize(numbered(3)), encoding="utf-8")
        code, _, err = run_cli("translate", str(other), "--bundle", b_path, "--provider", "fake")
        self.assertEqual(code, 2)
        self.assertIn("the bundle was made for 5 cues", err)

    def test_translate_writes_a_report_next_to_the_output(self):
        code, _, err = run_cli("translate", str(self.src), "--glossary", str(self.glossary), "--provider", "fake",
                               "--source", "en")
        self.assertEqual(code, 0, err)
        report = json.loads(Path(self.path("Movie.vi.report.json")).read_text(encoding="utf-8"))
        self.assertEqual(report["cues"], 5)
        self.assertEqual(len(report["bundle_hash"]), 64)
        self.assertIn("review", report)

    def test_old_bundle_runs_through_the_cli(self):
        """Criterion 18h at the CLI: a bundle without the optional fields still translates."""
        b_path = self.path("old.json")
        run_cli("bundle", str(self.src), "-o", b_path)
        data = json.loads(Path(b_path).read_text(encoding="utf-8"))
        for key in ("few_shot", "sections", "keep", "requested"):
            data.pop(key)
        Path(b_path).write_text(json.dumps(data), encoding="utf-8")
        code, _, err = run_cli("translate", str(self.src), "--bundle", b_path, "--provider", "fake",
                               "--out", self.path("old.srt"))
        self.assertEqual(code, 0, err)


class KeyOrderTests(unittest.TestCase):
    """FR-21.4 / criterion 18e: flag > environment > file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.key_file = Path(self._tmp.name) / "key.txt"
        self.key_file.write_text("\n  file-key  \n", encoding="utf-8")

    def test_flag_beats_environment_beats_file(self):
        env = {"GEMINI_API_KEY": "env-key"}
        self.assertEqual(cli.resolve_api_key("flag-key", "GEMINI_API_KEY", str(self.key_file), env), "flag-key")
        self.assertEqual(cli.resolve_api_key(None, "GEMINI_API_KEY", str(self.key_file), env), "env-key")
        self.assertEqual(cli.resolve_api_key(None, "GEMINI_API_KEY", str(self.key_file), {}), "file-key")
        self.assertEqual(cli.resolve_api_key(None, "GEMINI_API_KEY", None, {}), "")

    def test_google_key_is_a_fallback_for_gemini_only(self):
        """A Google key is offered to Gemini and never to another provider's server."""
        self.assertEqual(
            cli.resolve_api_key(None, "GEMINI_API_KEY", None, {"GOOGLE_API_KEY": "g"}, also=("GOOGLE_API_KEY",)), "g")
        self.assertEqual(cli.resolve_api_key(None, "GEMINI_API_KEY", None, {"GOOGLE_API_KEY": "g"}), "")
        self.assertEqual(cli.resolve_api_key(None, "MY_KEY", None, {"GOOGLE_API_KEY": "g"}), "")

    def test_openai_compat_never_reads_the_google_key(self):
        args = cli.build_parser().parse_args(
            ["translate", "x.srt", "--provider", "openai_compat", "--base-url", "https://example.test/v1", "--model", "m"])
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "google-secret"}, clear=True), \
                mock.patch("subcore.providers.openai_compat.OpenAICompatProvider") as provider:
            cli._build_provider(args)
        self.assertEqual(provider.call_args.args[1], "")


class CliGuardTests(CliTestBase):
    def test_existing_output_is_reported_before_a_provider_is_built(self):
        """No key needed to learn the file is there: nothing may cost money or fail on a missing key first."""
        (self.dir / "Movie.vi.srt").write_text("hand made", encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True):
            code, _, err = run_cli("translate", str(self.src), "--source", "en")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_an_existing_report_is_not_overwritten_without_force(self):
        report = self.dir / "Movie.vi.report.json"
        report.write_text("keep me", encoding="utf-8")
        code, _, err = run_cli("translate", str(self.src), "--provider", "fake", "--source", "en")
        self.assertEqual(code, 2)
        self.assertIn("report.json", err)
        self.assertEqual(report.read_text(encoding="utf-8"), "keep me")
        self.assertFalse((self.dir / "Movie.vi.srt").exists())
        code, _, _ = run_cli("translate", str(self.src), "--provider", "fake", "--source", "en", "--force")
        self.assertEqual(code, 0)
        self.assertNotEqual(report.read_text(encoding="utf-8"), "keep me")

    def test_a_bundle_cannot_be_moved_to_another_episode_by_a_flag(self):
        """Its glossary is already narrowed to its own episode; narrowing it again would lose terms silently."""
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "--glossary", str(self.glossary), "--season", "1", "--episode", "2", "-o", b_path)
        code, _, err = run_cli("translate", str(self.src), "--bundle", b_path, "--episode", "6", "--provider", "fake")
        self.assertEqual(code, 2)
        self.assertIn("narrowed", err)

    def test_the_provider_is_built_for_the_model_of_the_bundle(self):
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "--model", "gemini-from-the-bundle", "-o", b_path)
        args = cli.build_parser().parse_args(["translate", str(self.src), "--bundle", b_path])
        self.assertEqual(cli._bundle_model(args), "gemini-from-the-bundle")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=True), \
                mock.patch("subcore.providers.gemini.GeminiProvider") as gemini:
            cli._build_provider(args, cli._bundle_model(args))
        self.assertEqual(gemini.call_args.args, ("k", "gemini-from-the-bundle"))
        args.model = "flag-wins"
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=True), \
                mock.patch("subcore.providers.gemini.GeminiProvider") as gemini:
            cli._build_provider(args, cli._bundle_model(args))
        self.assertEqual(gemini.call_args.args, ("k", "flag-wins"))

    def test_a_damaged_bundle_file_is_a_clean_error(self):
        bad = self.dir / "bad.json"
        bad.write_text(json.dumps({"bundle_version": 1, "keep": [{"text": "x"}], "cues": []}), encoding="utf-8")
        code, _, err = run_cli("render-prompt", "--bundle", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("damaged bundle", err)


class ValidateTests(CliTestBase):
    def test_review_finds_each_problem_kind(self):
        src = make_cues(["Kyber crystal glows.", "Hello there", "Just words", "one two three"])
        out = [
            src[0].with_text("Tinh thể glows"),  # locked term missing
            src[1].with_text("<i>Xin chào</i>"),  # tags do not match
            src[2].with_text("Just words"),  # the source text itself
            src[3].with_text("x" * 60),  # line too long and too fast for its time
        ]
        kinds = {(f.cue, f.kind, f.severity) for f in review(src, out, load_glossary(GLOSSARY_YAML))}
        self.assertIn((1, "glossary", "error"), kinds)
        self.assertIn((2, "tags", "error"), kinds)
        self.assertIn((3, "untranslated", "warning"), kinds)
        self.assertIn((4, "line_length", "warning"), kinds)
        self.assertIn((4, "cps", "warning"), kinds)

    def test_count_mismatch_is_an_error_for_the_file(self):
        errors = [f for f in review(numbered(3), numbered(2)) if f.severity == "error"]
        self.assertEqual([(f.cue, f.kind) for f in errors], [(0, "count")])

    def test_cli_validate_matches_the_library_and_exits_one_on_errors(self):
        """Criterion 18f: the CLI and the worker call the same function, so they agree on the same texts."""
        translated = self.dir / "t.srt"
        translated.write_text(srt.serialize([c.with_text("không có thuật ngữ") for c in self.cues]), encoding="utf-8")
        code, out, _ = run_cli("validate", str(self.src), str(translated), "--glossary", str(self.glossary),
                               "--format", "json")
        self.assertEqual(code, 1)
        expected = review(srt.parse_file(self.src), srt.parse_file(translated), load_glossary(GLOSSARY_YAML))
        self.assertEqual(json.loads(out), [f.to_dict() for f in expected])

    def test_clean_translation_exits_zero(self):
        translated = self.dir / "clean.srt"
        translated.write_text(srt.serialize([c.with_text("Tinh thể Kyber " + str(c.index)) for c in self.cues]),
                              encoding="utf-8")
        code, _, _ = run_cli("validate", str(self.src), str(translated), "--glossary", str(self.glossary))
        self.assertEqual(code, 0)


class CompareTests(CliTestBase):
    def test_compare_two_bundles_prints_a_table(self):
        """Criterion 18g: two configurations on the same sample, numbers side by side."""
        a, b = self.path("a.json"), self.path("b.json")
        run_cli("bundle", str(self.src), "-o", a)
        run_cli("bundle", str(self.src), "--glossary", str(self.glossary), "-o", b)
        code, out, err = run_cli("compare", str(self.src), "--bundle", a, "--bundle", b, "--provider", "fake")
        self.assertEqual(code, 0, err)
        self.assertIn("model calls", out)
        self.assertIn("cues that differ", out)
        self.assertIn("1:a", out)
        self.assertIn("2:b", out)

    def test_compare_against_golden_reports_exact_and_similarity(self):
        golden = self.dir / "golden.srt"
        golden.write_text(srt.serialize([c.with_text(f"[vi] Kyber crystal {c.index} glows") for c in self.cues]),
                          encoding="utf-8")
        a = self.path("a.json")
        run_cli("bundle", str(self.src), "-o", a)
        code, out, err = run_cli("compare", str(self.src), "--bundle", a, "--golden", str(golden), "--provider", "fake")
        self.assertEqual(code, 0, err)
        self.assertIn("golden: exact cues", out)
        self.assertIn("5", out)

    def test_compare_needs_two_bundles_or_one_with_golden(self):
        a = self.path("a.json")
        run_cli("bundle", str(self.src), "-o", a)
        code, _, err = run_cli("compare", str(self.src), "--bundle", a, "--provider", "fake")
        self.assertEqual(code, 2)
        self.assertIn("compare needs", err)


class BundleCommandTests(CliTestBase):
    def test_bundle_command_writes_a_bundle_without_calling_a_model(self):
        b_path = self.path("b.json")
        with mock.patch.object(cli, "_build_provider", side_effect=AssertionError("provider built")):
            code, _, err = run_cli("bundle", str(self.src), "--glossary", str(self.glossary), "-o", b_path)
        self.assertEqual(code, 0, err)
        data = json.loads(Path(b_path).read_text(encoding="utf-8"))
        self.assertEqual(data["bundle_version"], 2)
        self.assertEqual(len(data["cues"]), 5)

    def test_bundle_command_refuses_to_overwrite(self):
        b_path = self.path("b.json")
        Path(b_path).write_text("x", encoding="utf-8")
        code, _, err = run_cli("bundle", str(self.src), "-o", b_path)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)


if __name__ == "__main__":
    unittest.main()
