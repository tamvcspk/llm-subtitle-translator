"""Contract of the VersionRepo port (SPEC criterion 10b): the same checks on SQLite and on an in-memory fake."""

import tempfile
import time
import unittest
from pathlib import Path

from gst_worker.ports import CURRENT, SUPERSEDED, TranslationVersion, VersionRepo
from gst_worker.store import Store

OUT = "/media/Show.S01E01.vi.srt"


class InMemoryVersionRepo:
    """Fake VersionRepo for tests that do not need a database."""

    def __init__(self) -> None:
        self._rows: list[TranslationVersion] = []

    def current_version(self, output_path):
        return next((r for r in self._rows if r.output_path == output_path and r.status == CURRENT), None)

    def versions(self, output_path):
        return sorted((r for r in self._rows if r.output_path == output_path), key=lambda r: r.version)

    def get_version(self, output_path, version):
        return next((r for r in self._rows if r.output_path == output_path and r.version == version), None)

    def next_version(self, output_path):
        return max((r.version for r in self._rows if r.output_path == output_path), default=0) + 1

    def add_version(self, output_path, *, version, stored_path, sha256, trigger, make_current=True, missing_units=(),
                    **fields):
        if make_current:
            self._rows = [
                TranslationVersion(**{**r.__dict__, "status": SUPERSEDED})
                if r.output_path == output_path and r.status == CURRENT else r
                for r in self._rows
            ]
        self._rows.append(TranslationVersion(
            output_path=output_path, version=version, stored_path=stored_path, sha256=sha256, trigger=trigger,
            status=CURRENT if make_current else SUPERSEDED, created_at=time.time(),
            missing_units=tuple(missing_units), **fields,
        ))

    def cached_versions(self, source_sha256, target_lang, context_hash):
        hits = [
            r for r in self._rows
            if (r.source_sha256, r.target_lang, r.context_hash) == (source_sha256, target_lang, context_hash)
            and r.trigger in ("auto", "retranslate")
        ]
        return list(reversed(hits))


def add(repo: VersionRepo, version: int, *, output=OUT, trigger="auto", **kwargs):
    repo.add_version(output, version=version, stored_path=f"/b/v{version}", sha256=f"sha{version}",
                     trigger=trigger, **kwargs)


class VersionRepoContract:
    """Mixed into a TestCase that provides ``self.repo``."""

    repo: VersionRepo

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.repo, VersionRepo)

    def test_empty_repo(self):
        self.assertIsNone(self.repo.current_version(OUT))
        self.assertIsNone(self.repo.get_version(OUT, 1))
        self.assertEqual(self.repo.versions(OUT), [])
        self.assertEqual(self.repo.next_version(OUT), 1)
        self.assertEqual(self.repo.cached_versions("s", "vi", "c"), [])

    def test_a_version_comes_back_as_a_named_immutable_value_with_defaults(self):
        add(self.repo, 1)
        got = self.repo.get_version(OUT, 1)
        self.assertIsInstance(got, TranslationVersion)
        self.assertEqual((got.version, got.stored_path, got.sha256, got.trigger, got.status),
                         (1, "/b/v1", "sha1", "auto", CURRENT))
        self.assertEqual((got.parent_version, got.unit_id, got.model, got.glossary_hash, got.memory_hash),
                         (None, None, "", "", ""))
        self.assertIs(got.context_complete, True)
        self.assertEqual(got.missing_units, ())
        self.assertGreater(got.created_at, 0)
        with self.assertRaises(AttributeError):
            got.sha256 = "other"

    def test_every_field_round_trips(self):
        add(self.repo, 1, source_path="/m/en.srt", source_sha256="src", target_lang="vi", context_hash="ctx",
            parent_version=None, model="m-1", tokens_in=7, tokens_out=9, unit_id=42, glossary_hash="g",
            context_complete=False, memory_hash="mem", missing_units=["S01E03", "S01E04"])
        got = self.repo.current_version(OUT)
        self.assertEqual(
            (got.source_path, got.source_sha256, got.target_lang, got.context_hash, got.model, got.tokens_in,
             got.tokens_out, got.unit_id, got.glossary_hash, got.memory_hash),
            ("/m/en.srt", "src", "vi", "ctx", "m-1", 7, 9, 42, "g", "mem"),
        )
        self.assertIs(got.context_complete, False)
        self.assertEqual(got.missing_units, ("S01E03", "S01E04"))

    def test_a_new_current_version_supersedes_the_previous_one(self):
        add(self.repo, 1)
        add(self.repo, 2, trigger="retranslate", parent_version=1)
        self.assertEqual(self.repo.current_version(OUT).version, 2)
        self.assertEqual([(v.version, v.status) for v in self.repo.versions(OUT)], [(1, SUPERSEDED), (2, CURRENT)])
        self.assertEqual(self.repo.get_version(OUT, 2).parent_version, 1)
        self.assertEqual(self.repo.next_version(OUT), 3)

    def test_a_version_added_without_make_current_leaves_the_current_one_alone(self):
        add(self.repo, 1)
        add(self.repo, 2, trigger="external", make_current=False)
        self.assertEqual(self.repo.current_version(OUT).version, 1)
        self.assertEqual(self.repo.get_version(OUT, 2).status, SUPERSEDED)

    def test_files_have_separate_histories(self):
        add(self.repo, 1)
        add(self.repo, 1, output="/media/Other.vi.srt")
        self.assertEqual(self.repo.next_version(OUT), 2)
        self.assertEqual(self.repo.current_version("/media/Other.vi.srt").output_path, "/media/Other.vi.srt")
        self.assertEqual(len(self.repo.versions(OUT)), 1)

    def test_cache_lookup_matches_input_and_context_and_ignores_other_triggers_newest_first(self):
        keys = dict(source_sha256="src", target_lang="vi", context_hash="ctx")
        add(self.repo, 1, **keys)
        add(self.repo, 2, trigger="retranslate", **keys)
        add(self.repo, 3, trigger="external", **keys)
        add(self.repo, 4, trigger="cue_edit", **keys)
        add(self.repo, 5, source_sha256="src", target_lang="vi", context_hash="other")
        hits = self.repo.cached_versions("src", "vi", "ctx")
        self.assertEqual([h.version for h in hits], [2, 1])
        self.assertEqual(self.repo.cached_versions("src", "fr", "ctx"), [])


class SqliteVersionRepoTests(VersionRepoContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Store(Path(directory.name) / "app.db")
        self.addCleanup(self.repo.close)


class InMemoryVersionRepoTests(VersionRepoContract, unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryVersionRepo()


class TranslationVersionsOnAFakeTests(unittest.TestCase):
    """The publish/revert rules need only the port: no SQLite in this test."""

    def test_publish_retranslate_and_revert_through_the_port(self):
        from gst_worker.versions import AUTO, RETRANSLATE, TranslationVersions

        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / "media" / "Show.vi.srt")
            Path(output).parent.mkdir()
            versions = TranslationVersions(InMemoryVersionRepo(), Path(tmp) / "backups")
            versions.publish(output, "one\n", trigger=AUTO)
            versions.publish(output, "two\n", trigger=RETRANSLATE)
            self.assertEqual(Path(output).read_text(encoding="utf-8"), "two\n")
            result = versions.revert(output, 1)
            self.assertEqual((result.status, result.version), ("reverted", 3))
            self.assertEqual(Path(output).read_text(encoding="utf-8"), "one\n")
            self.assertTrue(versions.is_system_file(output))


if __name__ == "__main__":
    unittest.main()
