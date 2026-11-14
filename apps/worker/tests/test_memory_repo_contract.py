"""Contract of the MemoryRepo port (SPEC criterion 10b): the same checks on SQLite and on an in-memory fake."""

import itertools
import tempfile
import time
import unittest
from pathlib import Path

from subcore.memory import Character, Facts

from gst_worker.ports import MemoryRepo, StoredFacts
from gst_worker.store import Store

PV = "x3"


class InMemoryMemoryRepo:
    """Fake MemoryRepo for tests that do not need a database."""

    def __init__(self) -> None:
        self._facts: dict[tuple[int, str, str], StoredFacts] = {}
        self._tick = itertools.count(1)
        self._order: dict[tuple[int, str, str], int] = {}
        self._summaries: dict[str, str] = {}
        self._snapshots: dict[str, list[tuple[str, str]]] = {}

    def get_facts(self, unit_id, source_sha256, prompt_version, *, touch=False):
        key = (unit_id, source_sha256, prompt_version)
        found = self._facts.get(key)
        if found is not None and touch:
            self._order[key] = next(self._tick)
        return found

    def save_facts(self, unit_id, source_sha256, prompt_version, *, model, facts, tokens_in=0, tokens_out=0):
        key = (unit_id, source_sha256, prompt_version)
        self._facts[key] = StoredFacts(
            unit_id, source_sha256, prompt_version, model, facts, facts.hash, tokens_in, tokens_out, time.time()
        )
        self._order[key] = next(self._tick)

    def latest_facts(self, unit_ids, prompt_version):
        wanted = set(unit_ids)
        keys = sorted((k for k in self._facts if k[0] in wanted and k[2] == prompt_version), key=self._order.get)
        return {k[0]: self._facts[k] for k in keys}

    def get_summary(self, input_hash):
        return self._summaries.get(input_hash)

    def put_summary(self, series_id, up_to_unit, input_hash, text):
        self._summaries[input_hash] = text

    def put_snapshot(self, memory_hash, sections):
        self._snapshots[memory_hash] = [(str(a), str(b)) for a, b in sections]

    def get_snapshot(self, memory_hash):
        return self._snapshots.get(memory_hash)


def facts(name="Ted", summary="") -> Facts:
    return Facts(characters=(Character(name, ("Teddy",), "m", "30s", "lead"),), summary=summary)


class MemoryRepoContract:
    """Mixed into a TestCase that provides ``self.repo``. Facts need a unit row, made by ``self.unit()``."""

    repo: MemoryRepo

    def unit(self) -> int:
        return len(getattr(self, "_units", [])) + 1

    def series(self) -> int:
        return 1

    def test_satisfies_the_port(self):
        self.assertIsInstance(self.repo, MemoryRepo)

    def test_empty_repo(self):
        self.assertIsNone(self.repo.get_facts(self.unit(), "sha", PV))
        self.assertEqual(self.repo.latest_facts([1, 2], PV), {})
        self.assertEqual(self.repo.latest_facts([], PV), {})
        self.assertIsNone(self.repo.get_summary("h"))
        self.assertIsNone(self.repo.get_snapshot("h"))

    def test_facts_round_trip_as_a_named_value_with_real_facts(self):
        uid = self.unit()
        self.repo.save_facts(uid, "sha", PV, model="m-1", facts=facts(summary="Ted meets Robin"), tokens_in=5, tokens_out=3)
        got = self.repo.get_facts(uid, "sha", PV)
        self.assertIsInstance(got, StoredFacts)
        self.assertIsInstance(got.facts, Facts)
        self.assertEqual(got.facts, facts(summary="Ted meets Robin"))
        self.assertEqual((got.unit_id, got.source_sha256, got.prompt_version, got.model, got.tokens_in, got.tokens_out),
                         (uid, "sha", PV, "m-1", 5, 3))
        self.assertEqual(got.facts_hash, got.facts.hash)
        with self.assertRaises(AttributeError):
            got.model = "other"

    def test_facts_are_keyed_by_unit_source_and_prompt_version(self):
        uid = self.unit()
        self.repo.save_facts(uid, "a", PV, model="m", facts=facts("A"))
        self.assertIsNone(self.repo.get_facts(uid, "b", PV))
        self.assertIsNone(self.repo.get_facts(uid, "a", "other-version"))
        self.assertIsNotNone(self.repo.get_facts(uid, "a", PV))

    def test_saving_again_replaces_the_facts_for_the_same_key(self):
        uid = self.unit()
        self.repo.save_facts(uid, "a", PV, model="m", facts=facts("Old"))
        self.repo.save_facts(uid, "a", PV, model="m2", facts=facts("New"))
        got = self.repo.get_facts(uid, "a", PV)
        self.assertEqual((got.model, got.facts.characters[0].name), ("m2", "New"))

    def test_latest_facts_is_the_newest_saved_or_touched_per_unit(self):
        uid = self.unit()
        self.repo.save_facts(uid, "a", PV, model="m", facts=facts("A"))
        time.sleep(0.02)
        self.repo.save_facts(uid, "b", PV, model="m", facts=facts("B"))
        self.assertEqual(self.repo.latest_facts([uid], PV)[uid].source_sha256, "b")
        time.sleep(0.02)
        self.repo.get_facts(uid, "a", PV)  # a plain read does not move it
        self.assertEqual(self.repo.latest_facts([uid], PV)[uid].source_sha256, "b")
        self.repo.get_facts(uid, "a", PV, touch=True)
        self.assertEqual(self.repo.latest_facts([uid], PV)[uid].source_sha256, "a")

    def test_latest_facts_only_returns_requested_units_and_prompt_version(self):
        first, second = self.unit(), self.unit()
        self.repo.save_facts(first, "a", PV, model="m", facts=facts("A"))
        self.repo.save_facts(second, "b", "old", model="m", facts=facts("B"))
        self.assertEqual(set(self.repo.latest_facts([first, second], PV)), {first})
        self.assertEqual(self.repo.latest_facts([second], PV), {})
        self.assertEqual(set(self.repo.latest_facts([second], "old")), {second})

    def test_summaries_are_cached_by_input_hash(self):
        series = self.series()
        self.repo.put_summary(series, 7, "h1", "the arc so far")
        self.assertEqual(self.repo.get_summary("h1"), "the arc so far")
        self.assertIsNone(self.repo.get_summary("h2"))
        self.repo.put_summary(series, 8, "h1", "revised")
        self.assertEqual(self.repo.get_summary("h1"), "revised")

    def test_snapshots_keep_sections_as_title_body_pairs(self):
        self.repo.put_snapshot("fp", [("Characters", "Ted"), ("Recent", "…")])
        got = self.repo.get_snapshot("fp")
        self.assertEqual(got, [("Characters", "Ted"), ("Recent", "…")])
        self.assertTrue(all(isinstance(pair, tuple) for pair in got))
        self.assertIsNone(self.repo.get_snapshot("other"))


class SqliteMemoryRepoTests(MemoryRepoContract, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "app.db")
        self.addCleanup(self.store.close)
        self.repo = self.store
        self._units = []

    def series(self) -> int:
        return self.store.upsert_series("s", title="Show")

    def unit(self) -> int:
        series_id = self.series()
        unit_id = self.store.upsert_unit(
            series_id=series_id, season=1, episode=len(self._units) + 1, title="", media_type="series",
            bazarr_id="", video_path="", source_path="", source_code="en",
        )
        self._units.append(unit_id)
        return unit_id


class InMemoryMemoryRepoTests(MemoryRepoContract, unittest.TestCase):
    def setUp(self):
        self.repo = InMemoryMemoryRepo()
        self._units = []

    def unit(self) -> int:
        self._units.append(len(self._units) + 1)
        return self._units[-1]


if __name__ == "__main__":
    unittest.main()
