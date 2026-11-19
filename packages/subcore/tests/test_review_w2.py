"""Review wave W2, F9 (R11-1 to R11-9): the CLI and the bundle keep their promises. One test per finding."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from subcore import cli, srt
from subcore.bundle import BundleError, TranslationBundle
from subcore.providers import FakeProvider, Unavailable
from subcore.providers.base import Capabilities
from subcore.translate import PromptContext, TranslationError
from subcore.translate.engine import make_bundle, render_requests, run

from .helpers import numbered


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cues = numbered(5, "Kyber crystal {n} glows")
        self.src = self.dir / "Movie.en.srt"
        self.src.write_text(srt.serialize(self.cues), encoding="utf-8")

    def path(self, name):
        return str(self.dir / name)


class KeyOfAnotherProviderTests(Base):
    """R11-1: a Gemini key is never the bearer token of a third-party host."""

    def parse(self, *extra):
        return cli.build_parser().parse_args(
            ["translate", "x.srt", "--provider", "openai_compat", "--base-url", "https://third.party/v1", "--model", "m", *extra])

    def key_sent(self, args, env):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("subcore.providers.openai_compat.OpenAICompatProvider") as provider:
            cli._build_provider(args)
        return provider.call_args.args[1]

    def test_openai_compat_does_not_read_the_gemini_variable(self):
        self.assertEqual(self.key_sent(self.parse(), {"GEMINI_API_KEY": "gemini-secret"}), "")

    def test_openai_compat_reads_its_own_variable(self):
        self.assertEqual(self.key_sent(self.parse(), {"OPENAI_COMPAT_API_KEY": "its-own"}), "its-own")

    def test_a_variable_named_on_purpose_is_honoured(self):
        self.assertEqual(self.key_sent(self.parse("--api-key-env", "MY_KEY"), {"MY_KEY": "chosen"}), "chosen")

    def test_gemini_still_reads_the_gemini_variable_and_names_it_in_the_error(self):
        args = cli.build_parser().parse_args(["translate", "x.srt"])
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=True), \
                mock.patch("subcore.providers.gemini.GeminiProvider") as gemini:
            cli._build_provider(args)
        self.assertEqual(gemini.call_args.args[0], "k")
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as raised:
            cli._build_provider(args)
        self.assertIn("GEMINI_API_KEY", str(raised.exception))

    def test_the_memory_command_follows_the_same_rule(self):
        args = cli.build_parser().parse_args(["memory", "a.srt", "--provider", "openai_compat", "--base-url",
                                              "https://third.party/v1", "--model", "m"])
        self.assertEqual(self.key_sent(args, {"GEMINI_API_KEY": "gemini-secret"}), "")


class TargetFlagTests(Base):
    def test_target_flag_drops_the_name_the_bundle_gave_the_old_language(self):
        """R11-2: the prompt must not say "Vietnamese (northern)" while the rules and the report say French."""
        bundle = make_bundle(self.cues, PromptContext(target_lang="vi", target_name="Vietnamese (northern)"), model="m")
        b_path = self.path("b.json")
        Path(b_path).write_text(bundle.to_json(), encoding="utf-8")
        code, out, err = run_cli("render-prompt", "--bundle", b_path, "--target", "fr")
        self.assertEqual(code, 0, err)
        self.assertIn("into French", out)
        self.assertNotIn("northern", out)
        code, out, _ = run_cli("render-prompt", "--bundle", b_path)  # no flag: the bundle's own name stays
        self.assertIn("Vietnamese (northern)", out)

    def test_the_same_language_keeps_the_name(self):
        bundle = make_bundle(self.cues, PromptContext(target_lang="zh", target_name="Simplified Chinese"), model="m")
        b_path = self.path("b.json")
        Path(b_path).write_text(bundle.to_json(), encoding="utf-8")
        _, out, _ = run_cli("render-prompt", "--bundle", b_path, "--target", "ZH")
        self.assertIn("Simplified Chinese", out)


class UnsetModelTests(Base):
    """R11-3 (D6): what nobody chose is not written down and not hashed; the provider fills it in at run time."""

    def test_a_bundle_written_without_a_provider_has_no_model_and_no_output_limit(self):
        b_path = self.path("b.json")
        self.assertEqual(run_cli("bundle", str(self.src), "-o", b_path)[0], 0)
        params = json.loads(Path(b_path).read_text(encoding="utf-8"))["params"]
        self.assertNotIn("model", params)
        self.assertNotIn("max_output_tokens", params)

    def test_running_it_gives_the_hash_of_the_file(self):
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "-o", b_path)
        file_hash = TranslationBundle.from_json(Path(b_path).read_text(encoding="utf-8")).bundle_hash()
        code, _, err = run_cli("translate", str(self.src), "--bundle", b_path, "--provider", "fake", "--out", self.path("o.srt"))
        self.assertEqual(code, 0, err)
        report = json.loads(Path(self.path("o.report.json")).read_text(encoding="utf-8"))
        self.assertEqual(report["bundle_hash"], file_hash)

    def test_a_translate_without_a_bundle_hashes_like_the_bundle_command(self):
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "--source", "en", "-o", b_path)
        run_cli("translate", str(self.src), "--provider", "fake", "--source", "en")
        report = json.loads(Path(self.path("Movie.vi.report.json")).read_text(encoding="utf-8"))
        self.assertEqual(report["bundle_hash"], TranslationBundle.from_json(Path(b_path).read_text(encoding="utf-8")).bundle_hash())

    def test_a_value_the_user_set_is_kept_and_hashed(self):
        a, b = self.path("a.json"), self.path("b.json")
        run_cli("bundle", str(self.src), "-o", a)
        run_cli("bundle", str(self.src), "--model", "m1", "--max-output-tokens", "4000", "-o", b)
        data = json.loads(Path(b).read_text(encoding="utf-8"))["params"]
        self.assertEqual((data["model"], data["max_output_tokens"]), ("m1", 4000))
        load = lambda p: TranslationBundle.from_json(Path(p).read_text(encoding="utf-8")).bundle_hash()  # noqa: E731
        self.assertNotEqual(load(a), load(b))

    def test_the_providers_output_limit_plans_the_parts_when_the_bundle_has_none(self):
        small = FakeProvider(caps=Capabilities(max_output_tokens=2000))  # too small for 400 cues in one call
        unset = make_bundle(numbered(400), PromptContext(target_lang="vi"))
        self.assertEqual(unset.params.max_output_tokens, 0)
        run(unset, small)
        self.assertGreater(len(small.calls), 1)
        explicit = make_bundle(numbered(400), PromptContext(target_lang="vi"), max_output_tokens=65_536)
        big = FakeProvider(caps=Capabilities(max_output_tokens=2000))
        run(explicit, big)
        self.assertEqual(len([c for c in big.calls if c.meta["kind"] == "translate"]), 1)  # the bundle's own limit wins
        shown = render_requests(unset, small)
        self.assertEqual(len(shown), len([c for c in small.calls if c.meta["kind"] == "translate"]))

    def test_the_model_and_the_limit_are_still_part_of_the_hash_when_set(self):
        a = make_bundle(numbered(3), model="m1", max_output_tokens=4000)
        self.assertNotEqual(a.bundle_hash(), make_bundle(numbered(3), model="m2", max_output_tokens=4000).bundle_hash())
        self.assertNotEqual(a.bundle_hash(), make_bundle(numbered(3), model="m1", max_output_tokens=8000).bundle_hash())


class ValidateNarrowingTests(Base):
    """R11-4: validate reviews against the glossary of the episode, as the worker does."""

    def setUp(self):
        super().setUp()
        self.glossary = self.dir / "g.yaml"
        self.glossary.write_text(
            "version: 1\nterms:\n  - {source: Kyber crystal, target: tinh thể Kyber, locked: true, from: S02E05}\n",
            encoding="utf-8")
        self.translated = self.dir / "t.srt"
        self.translated.write_text(srt.serialize([c.with_text("không có gì") for c in self.cues]), encoding="utf-8")

    def validate(self, *extra):
        return run_cli("validate", str(self.src), str(self.translated), "--glossary", str(self.glossary), *extra)

    def test_a_term_that_starts_later_is_not_demanded_in_an_earlier_episode(self):
        self.assertEqual(self.validate()[0], 1)  # the whole glossary: the term counts
        self.assertEqual(self.validate("--season", "1", "--episode", "1")[0], 0)
        self.assertEqual(self.validate("--season", "2", "--episode", "5")[0], 1)

    def test_a_bundle_brings_its_narrowed_glossary(self):
        b_early, b_late = self.path("early.json"), self.path("late.json")
        for path, ep in ((b_early, "1"), (b_late, "5")):
            run_cli("bundle", str(self.src), "--glossary", str(self.glossary), "--season", "2" if ep == "5" else "1",
                    "--episode", ep, "-o", path)
        self.assertEqual(run_cli("validate", str(self.src), str(self.translated), "--bundle", b_early)[0], 0)
        self.assertEqual(run_cli("validate", str(self.src), str(self.translated), "--bundle", b_late)[0], 1)

    def test_a_flag_cannot_narrow_a_glossary_that_is_already_narrowed(self):
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "--glossary", str(self.glossary), "--season", "1", "--episode", "1", "-o", b_path)
        code, _, err = run_cli("validate", str(self.src), str(self.translated), "--bundle", b_path, "--season", "2")
        self.assertEqual(code, 2)
        self.assertIn("already narrowed", err)


class CompareFailureTests(Base):
    """R11-5: what was paid for is shown even when the other side fails, and the cost is announced."""

    def setUp(self):
        super().setUp()
        self.a, self.b = self.path("a.json"), self.path("b.json")
        run_cli("bundle", str(self.src), "-o", self.a)
        run_cli("bundle", str(self.src), "--title", "Other", "-o", self.b)

    def broken(self):
        def handler(req, call_no):
            raise Unavailable("overloaded")
        return FakeProvider(handler)

    def test_the_first_result_survives_the_failure_of_the_second(self):
        providers = [FakeProvider(), self.broken()]
        with mock.patch.object(cli, "_build_provider", side_effect=providers):
            code, out, err = run_cli("compare", str(self.src), "--bundle", self.a, "--bundle", self.b, "--provider", "fake")
        self.assertEqual(code, 1)
        self.assertIn("1:a", out)
        self.assertIn("2:b: provider failed: Unavailable", err)
        self.assertNotIn("Traceback", err)

    def test_a_translation_error_is_a_message_too(self):
        def drops_everything(req, call_no):
            return []
        providers = [FakeProvider(drops_everything), FakeProvider()]
        with mock.patch.object(cli, "_build_provider", side_effect=providers):
            code, out, err = run_cli("compare", str(self.src), "--bundle", self.a, "--bundle", self.b, "--provider", "fake")
        self.assertEqual(code, 1)
        self.assertIn("2:b", out)
        self.assertIn("1:a:", err)
        with self.assertRaises(TranslationError):
            run(TranslationBundle.from_json(Path(self.a).read_text(encoding="utf-8")), FakeProvider(drops_everything))

    def test_the_providers_are_built_before_the_first_paid_run(self):
        built = []

        def build(args, model=None, target=None):
            built.append(args.bundle)
            if len(built) == 2:
                raise SystemExit("error: no key")
            return FakeProvider()

        ran = []
        with mock.patch.object(cli, "_build_provider", side_effect=build), \
                mock.patch.object(cli, "run", side_effect=lambda *a, **k: ran.append(1)), \
                self.assertRaises(SystemExit):
            run_cli("compare", str(self.src), "--bundle", self.a, "--bundle", self.b)
        self.assertEqual(ran, [])

    def test_a_billed_provider_is_announced(self):
        with mock.patch.object(cli, "_build_provider", return_value=FakeProvider()):
            _, _, err = run_cli("compare", str(self.src), "--bundle", self.a, "--bundle", self.b, "--provider", "gemini")
        self.assertIn("2 translation(s)", err)
        _, _, quiet = run_cli("compare", str(self.src), "--bundle", self.a, "--bundle", self.b, "--provider", "fake")
        self.assertNotIn("billed", quiet)


class InputErrorTests(Base):
    """R11-6: a bad input is "error: ..." with code 2, never a traceback."""

    def test_a_missing_key_file(self):
        missing = self.path("nope.key")
        with mock.patch.dict(os.environ, {}, clear=True):
            code, _, err = run_cli("translate", str(self.src), "--api-key-file", missing)
        self.assertEqual(code, 2)
        self.assertIn("--api-key-file", err)
        self.assertNotIn(missing, err)

    def test_a_key_file_that_is_not_text(self):
        key = self.dir / "k.bin"
        key.write_bytes(b"\xff\xfe\x00\x80")
        with mock.patch.dict(os.environ, {}, clear=True):
            code, _, err = run_cli("translate", str(self.src), "--api-key-file", str(key))
        self.assertEqual(code, 2)
        self.assertIn("not UTF-8", err)

    def test_a_bundle_that_is_not_utf8(self):
        bad = self.dir / "bad.json"
        bad.write_bytes(b"\xff\xfe{}")
        code, _, err = run_cli("render-prompt", "--bundle", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("not UTF-8", err)

    def test_a_glossary_that_is_not_utf8_or_not_there(self):
        bad = self.dir / "g.yaml"
        bad.write_bytes(b"\xff\xfe\x00")
        for glossary in (str(bad), self.path("missing.yaml")):
            code, _, err = run_cli("translate", str(self.src), "--provider", "fake", "--glossary", glossary)
            self.assertEqual(code, 2, err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run_cli("check-glossary", str(bad))[0], 2)

    def test_a_subtitle_that_is_not_there(self):
        code, _, err = run_cli("translate", self.path("missing.srt"), "--provider", "fake")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


class BundleTypeTests(unittest.TestCase):
    """R11-7: a hand-edited bundle fails when it is read, before any call is paid for."""

    def data(self, **patch):
        data = make_bundle(numbered(3), PromptContext(target_lang="vi"), model="m", max_output_tokens=4000).to_dict()
        for dotted, value in patch.items():
            node = data
            *parents, last = dotted.split(".")
            for key in parents:
                node = node[key]
            node[last] = value
        return data

    def test_a_good_bundle_still_reads(self):
        TranslationBundle.from_dict(self.data())

    def test_wrong_types_are_refused_with_the_field_named(self):
        cases = [
            ("params.repair_rounds", "2"),
            ("params.min_part_cues", 2.5),
            ("params.context_window", True),
            ("params.max_output_tokens", "4000"),
            ("params.output_factor", "1.3"),
            ("params.output_factor", 0),
            ("params.temperature", "hot"),
            ("params.model", 7),
            ("episode.season", "1"),
            ("episode.episode", 1.5),
            ("requested", "2"),
            ("language", "vi"),
        ]
        for dotted, value in cases:
            with self.subTest(dotted, value=value):
                with self.assertRaises(BundleError) as raised:
                    TranslationBundle.from_dict(self.data(**{dotted: value}))
                self.assertIn(dotted.split(".")[-1], str(raised.exception))

    def test_a_cue_number_must_be_a_whole_number(self):
        for bad in ("5", 1.5, None):
            with self.subTest(bad):
                with self.assertRaises(BundleError):
                    TranslationBundle.from_dict(self.data(requested=[1, bad]))
        data = self.data(active_rules=[{"a": "A", "b": "B", "a_to_b": "x", "from_cue": "5"}])
        with self.assertRaises(BundleError) as raised:
            TranslationBundle.from_dict(data)
        self.assertIn("from_cue", str(raised.exception))

    def test_an_old_bundle_with_its_explicit_numbers_still_runs(self):
        data = self.data()
        data["params"]["max_output_tokens"] = 65_536
        data["params"]["model"] = ""
        bundle = TranslationBundle.from_dict(data)
        self.assertEqual(bundle.params.max_output_tokens, 65_536)
        run(bundle, FakeProvider())


class ReportTests(Base):
    """R11-8, R11-9."""

    def test_the_fake_provider_writes_the_language_of_the_bundle(self):
        b_path = self.path("fr.json")
        run_cli("bundle", str(self.src), "--target", "fr", "-o", b_path)
        code, _, err = run_cli("translate", str(self.src), "--bundle", b_path, "--provider", "fake", "--out", self.path("o.srt"))
        self.assertEqual(code, 0, err)
        self.assertTrue(Path(self.path("o.srt")).read_text(encoding="utf-8").count("[fr] ") == 5)

    def test_a_bundle_is_read_once_per_run(self):
        b_path = self.path("b.json")
        run_cli("bundle", str(self.src), "-o", b_path)
        with mock.patch.object(cli, "_load_bundle", wraps=cli._load_bundle) as load:
            code, _, err = run_cli("translate", str(self.src), "--bundle", b_path, "--provider", "fake", "--out", self.path("o.srt"))
        self.assertEqual(code, 0, err)
        self.assertEqual(load.call_count, 1)

    def test_the_report_is_written_after_the_subtitle_and_atomically(self):
        written = []
        real = srt.write_atomic

        def spy(path, text):
            written.append(Path(path).name)
            real(path, text)

        with mock.patch.object(cli.srt, "write_atomic", side_effect=spy):
            code, _, err = run_cli("translate", str(self.src), "--provider", "fake", "--source", "en")
        self.assertEqual(code, 0, err)
        self.assertEqual(written, ["Movie.vi.srt", "Movie.vi.report.json"])


if __name__ == "__main__":
    unittest.main()
