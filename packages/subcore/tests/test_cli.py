import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from subcore import cli, srt

from .helpers import numbered

SRC = srt.serialize(numbered(3, "Hello {n}"))


def run(*argv):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = cli.main(list(argv))
    return code, err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.src = self.dir / "Movie.en.srt"
        self.src.write_text(SRC, encoding="utf-8")

    def test_default_output_name_replaces_source_language(self):
        self.assertEqual(cli.default_output_path(self.src, "vi", "en").name, "Movie.vi.srt")
        self.assertEqual(cli.default_output_path(self.src, "vi", None).name, "Movie.vi.srt")
        self.assertEqual(cli.default_output_path(self.dir / "Plain.srt", "vi", None).name, "Plain.vi.srt")

    def test_translate_with_fake_provider(self):
        code, err = run("translate", str(self.src), "--provider", "fake", "--source", "en")
        self.assertEqual(code, 0, err)
        out = srt.parse_file(self.dir / "Movie.vi.srt")
        self.assertEqual([c.text for c in out], ["[vi] Hello 1", "[vi] Hello 2", "[vi] Hello 3"])
        self.assertEqual([c.timing for c in out], [c.timing for c in srt.parse(SRC)])

    def test_existing_output_is_never_overwritten_without_force(self):
        existing = self.dir / "Movie.vi.srt"
        existing.write_text("hand made", encoding="utf-8")
        code, err = run("translate", str(self.src), "--provider", "fake", "--source", "en")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(existing.read_text(encoding="utf-8"), "hand made")
        code, _ = run("translate", str(self.src), "--provider", "fake", "--source", "en", "--force")
        self.assertEqual(code, 0)
        self.assertNotEqual(existing.read_text(encoding="utf-8"), "hand made")

    def test_glossary_is_validated_before_any_call(self):
        bad = self.dir / "g.yaml"
        bad.write_text("terms:\n  - source: A\n", encoding="utf-8")
        code, err = run("translate", str(self.src), "--provider", "fake", "--glossary", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("terms[0]", err)
        self.assertFalse((self.dir / "Movie.vi.srt").exists())

    def test_check_glossary(self):
        good = self.dir / "g.yaml"
        good.write_text("terms:\n  - {source: A, target: B, locked: true}\n", encoding="utf-8")
        self.assertEqual(run("check-glossary", str(good))[0], 0)

    def test_memory_command_prints_and_saves_the_facts_of_each_episode(self):
        import json

        second = self.dir / "Movie.S01E02.en.srt"
        second.write_text(SRC, encoding="utf-8")
        saved = self.dir / "facts.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["memory", str(self.src), str(second), "--provider", "fake", "--save", str(saved)])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().count("(fake provider: no facts)"), 2)
        self.assertEqual(sorted(json.loads(saved.read_text(encoding="utf-8"))), ["Movie.S01E02.en.srt", "Movie.en.srt"])

    def test_memory_command_rejects_an_unreadable_file(self):
        code, err = run("memory", str(self.dir / "missing.srt"), "--provider", "fake")
        self.assertEqual(code, 2)
        self.assertIn("error", err)

    def test_missing_api_key_is_a_clear_error(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as ctx:
            run("translate", str(self.src))
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
