"""Review wave W4 (PLAN, "Code review P10-P14"): who may make a job and how often (R10-1, R10-4, R10-7, R10-8, R14-2), and
how the catalog is read and shown (R10-3, R10-6, R10-9, R14-3). A fake Bazarr and a temporary media folder; no network."""

import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from subserver import queue_policy as policy
from subserver import versions as versions_module
from subserver.auto_request import AutoRequest, AutoRequested, sync_and_request
from subserver.bazarr import BazarrMedia
from subserver.catalog_view import JobIndex
from subserver.ports import CatalogSubtitle

from .test_catalog import EN, SRT, CatalogCase, Env


class PolicyAndFailedJobTests(CatalogCase):
    """R10-1 (D4): the policy never retries or repeats what failed or what the admin cancelled; only the admin does."""

    def failed_job(self, env):
        sync_and_request(env.sync, env.auto)
        (job,) = env.jobs_open()
        env.queue.jobs.transition(job["job_id"], "running")
        env.queue.jobs.transition(job["job_id"], "failed", error="boom")
        return job["job_id"]

    def test_a_policy_job_that_failed_is_not_asked_for_again_by_the_policy(self):
        env = self.env(auto_request=True)
        env.episode(1)
        env.sync.run()
        job_id = self.failed_job(env)
        for _ in range(3):
            _, made = sync_and_request(env.sync, env.auto)
            self.assertEqual(made.created, 0)
        self.assertEqual(env.jobs_open(), [])
        self.assertEqual(env.queue.jobs.state_of(job_id), "failed")  # still there, waiting for the admin
        self.assertEqual(env.label("7101")["text"], "Missing · Failed")
        self.assertEqual(env.store.policy_jobs_since(0), 1)  # one job made by the policy, ever: the cap counts it once

    def test_the_planner_says_why_the_policy_leaves_it_alone(self):
        env = self.env(auto_request=True)
        env.episode(1)
        env.sync.run()
        self.failed_job(env)
        from subserver.job_requests import Selection
        plan = env.requests.plan(Selection(everything=True), automatic=True, measure=False)
        self.assertEqual([j.skip for j in plan.skipped], ["failed"])
        self.assertIn("only a manual request", plan.skipped[0].reason)

    def test_a_policy_job_the_admin_cancelled_is_not_made_again(self):
        env = self.env(auto_request=True)
        env.episode(1)
        env.sync.run()
        sync_and_request(env.sync, env.auto)
        (job,) = env.jobs_open()
        self.assertEqual(env.queue.jobs.request_stop(job["job_id"]), "cancelled")
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual((made.created, env.jobs_open()), (0, []))
        self.assertEqual(env.queue.jobs.state_of(job["job_id"]), "cancelled")

    def test_the_queue_itself_refuses_a_policy_enqueue_over_a_failed_job(self):
        """A second line of defence: ``enqueue`` does not trust the planner."""
        env = self.env(auto_request=True)
        env.episode(1)
        env.sync.run()
        job_id = self.failed_job(env)
        source, output = env.paths("7101")
        base = {"subtitle_path": source, "output_path": output, "source_code": "en", "source_language": "English",
                "video_path": env.item("7101").video_path}
        self.assertEqual(env.queue.enqueue(base, [{"code": "vi", "language": "Vietnamese", "enabled": True}], origin="policy"), [])
        self.assertEqual(env.queue.jobs.state_of(job_id), "failed")

    def test_the_admin_still_retries_it(self):
        env = self.env(auto_request=True)
        env.episode(1)
        env.sync.run()
        job_id = self.failed_job(env)
        made = env.actions.request({"items": env.ids("7101")})
        self.assertEqual(made["created"], [job_id])  # the same job, put back in line
        self.assertEqual(env.queue.jobs.state_of(job_id), "queued")


class CapTests(CatalogCase):
    """R10-4: ``capped`` is what the limit held back, not what ``enqueue`` skipped for another reason."""

    def test_the_cap_counts_what_it_kept_back(self):
        env = self.env(auto_request=True, auto_request_daily_cap=1)
        for n in (1, 2, 3):
            env.episode(n)
        env.sync.run()
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual((made.created, made.capped), (1, 2))

    def test_an_item_skipped_for_another_reason_is_not_capped(self):
        env = self.env()
        for n in (1, 2, 3):
            env.episode(n)
        env.sync.run()
        from subserver.job_requests import Selection
        plan = env.requests.plan(Selection(everything=True), automatic=False, measure=False)
        os.remove(env.paths("7102")[0])  # the source vanishes before the jobs are made
        made = env.requests.create(plan, origin="policy", prio=policy.AUTO, limit=5)
        self.assertEqual((len(made.created), made.capped), (2, 0))  # one was skipped; none was held back by the limit


class SyncNowTests(CatalogCase):
    """R10-7: Sync now asks, the background loop reads, and the policy never runs twice at once."""

    def test_the_request_only_asks_and_answers_with_the_state_as_it_is(self):
        env = self.env()
        env.episode(1)
        reads = []
        original = env.fake.catalog
        env.fake.catalog = lambda: (reads.append(1), original())[1]
        answer = env.actions.sync_now()
        self.assertEqual((answer["requested"], reads, env.store.catalog_count()), (True, [], 0))  # nothing was read
        self.assertTrue(env.sync.asked)
        self.assertTrue(env.sync.take_request())  # the loop takes it once ...
        self.assertFalse(env.sync.take_request())  # ... and only once
        env.sync.run()
        self.assertEqual(len(reads), 1)

    def test_the_loop_wakes_as_soon_as_a_read_is_asked_for(self):
        env = self.env()
        woke = []
        thread = threading.Thread(target=lambda: (env.sync.idle(30), woke.append(1)))
        thread.start()
        env.actions.sync_now()
        thread.join(5)
        self.assertEqual(woke, [1])

    def test_two_passes_of_the_policy_never_overlap(self):
        env = self.env(auto_request=True)
        env.episode(1)
        env.sync.run()
        env.auto._running.acquire()  # a pass is running
        try:
            self.assertEqual(env.auto.run(), AutoRequested())  # the second does nothing
            self.assertEqual(env.jobs_open(), [])
        finally:
            env.auto._running.release()
        self.assertEqual(env.auto.run().created, 1)


class AcceptShrinkTests(CatalogCase):
    """R10-6 (D3): a real, large shrink can be accepted by the admin, once."""

    def shrunk(self):
        env = self.env()  # guard 20 %
        for n in range(1, 10):
            env.episode(n)
        env.sync.run()  # 1 series + 9 episodes
        env.fake.entries = env.fake.entries[:7]
        self.assertEqual(env.sync.run().status, "kept")
        return env

    def test_the_guard_says_how_much_it_would_drop(self):
        env = self.shrunk()
        self.assertEqual(env.sync.shrink, (7, 3))
        self.assertEqual(env.actions.status()["shrink"], {"listed": 7, "dropped": 3})
        self.assertEqual(env.store.catalog_count(), 10)  # the cache is as it was

    def test_a_confirmed_read_goes_through_once_and_the_guard_comes_back(self):
        env = self.shrunk()
        env.actions.sync_now({"accept_shrink": True})
        state = env.sync.run()
        self.assertEqual((state.status, state.items, env.store.catalog_count()), ("ok", 7, 7))
        self.assertIsNone(env.actions.status()["shrink"])
        env.fake.entries = env.fake.entries[:4]  # another big shrink: the guard is on again
        self.assertEqual(env.sync.run().status, "kept")

    def test_an_ordinary_sync_now_does_not_bypass_the_guard(self):
        env = self.shrunk()
        env.actions.sync_now({})
        self.assertEqual(env.sync.run().status, "kept")

    def test_it_must_be_true_or_false(self):
        env = self.shrunk()
        with self.assertRaises(ValueError):
            env.actions.sync_now({"accept_shrink": "yes"})


class PagesTests(unittest.TestCase):
    """R10-3: a short page in the middle of a list is not its end when Bazarr says how many there are."""

    class Capped:
        """A server that caps each page at one item whatever length is asked for."""

        def __init__(self, total):
            self.total = total

        def get_json(self, url, params=None, headers=None):
            if url.endswith("/api/series") and params and "start" in params:
                start = params["start"]
                items = [{"title": f"S{n}", "sonarrSeriesId": n} for n in range(start + 1, min(start + 1, 3) + 1)]
                return {"data": items, **({"total": self.total} if self.total is not None else {})}
            if url.endswith("/api/episodes"):
                return {"data": []}
            return {"data": [], "total": 0}

    def titles(self, total):
        with patch("subserver.bazarr.CATALOG_PAGE_SIZE", 2):
            media = BazarrMedia(self.Capped(total), lambda: {"bazarr_url": "http://bazarr", "bazarr_api_key": "key"})
            return [e.title for e in media.catalog() if e.kind == "series"]

    def test_a_short_page_before_the_total_does_not_end_the_list(self):
        self.assertEqual(self.titles(3), ["S1", "S2", "S3"])

    def test_without_a_total_a_short_page_is_the_end(self):
        self.assertEqual(self.titles(None), ["S1"])


class BootstrapChoiceTests(CatalogCase):
    """R14-2: the first episodes are the first that still need a translation."""

    def env(self, **settings):
        env = Env(self, auto_request=True, auto_request_bootstrap=2, **settings)
        env.episode(1, vi=True)  # somebody else's Vietnamese subtitles
        env.episode(2, vi=True)
        env.eps = {n: env.episode(n) for n in (3, 4, 5)}
        env.sync.run()
        return env

    def test_episodes_that_already_have_a_subtitle_do_not_use_up_the_places(self):
        env = self.env()
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual(made.created, 2)
        self.assertEqual([env.label(b)["job"] for b in ("7103", "7104", "7105")], ["requested", "requested", ""])

    def test_the_wait_does_not_move_on_by_itself_once_those_are_translated(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        for job in env.jobs_open():
            env.queue.jobs.transition(job["job_id"], "running")
            env.queue.jobs.transition(job["job_id"], "done")
        for n in (3, 4):  # the worker made them, and Bazarr now lists them
            env.translated(f"710{n}")
            entry = env.eps[n]
            env.replace_entry(entry, missing=(), subtitles=(*entry.subtitles, CatalogSubtitle("vi", env.paths(f"710{n}")[1])))
        env.sync.run()
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual(made.created, 0)  # episode 5 still waits for the glossary: the wait did not move on
        page = env.view.series_page(env.item("7", "series").id)
        self.assertEqual(next(i for i in page["items"] if i["bazarr_id"] == "7105")["targets"][0]["reason"], "bootstrap")


class CatalogWritesTests(CatalogCase):
    """R10-9: a sync that finds nothing new writes nothing, and the policy off costs nothing."""

    def test_a_read_that_changes_nothing_rewrites_no_row(self):
        env = self.env()
        for n in (1, 2, 3):
            env.episode(n)
        env.sync.run()
        first = {r["bazarr_id"]: r["synced_at"] for r in env.store.query("SELECT bazarr_id, synced_at FROM catalog_items")}
        env.clock.advance(600)
        env.sync.run()
        again = {r["bazarr_id"]: r["synced_at"] for r in env.store.query("SELECT bazarr_id, synced_at FROM catalog_items")}
        self.assertEqual(again, first)
        self.assertEqual(env.store.query("SELECT COUNT(*) AS n FROM catalog_items WHERE gone_at IS NOT NULL")[0]["n"], 0)

    def test_only_the_row_that_changed_is_written_and_a_vanished_one_is_marked_gone(self):
        env = self.env(catalog_shrink_guard=0)
        a, b, c = env.episode(1), env.episode(2), env.episode(3)
        env.sync.run()
        env.clock.advance(600)
        env.replace_entry(b, missing=())  # Bazarr now has its subtitle
        env.fake.entries.remove(c)  # and episode 3 is no longer listed
        state = env.sync.run()
        rows = {r["bazarr_id"]: (r["synced_at"], r["gone_at"]) for r in env.store.query("SELECT * FROM catalog_items")}
        self.assertEqual(rows["7101"][0] < rows["7102"][0], True)  # only the changed one was written
        self.assertIsNone(rows["7102"][1])
        self.assertIsNotNone(rows["7103"][1])
        self.assertEqual((state.items, env.store.catalog_count()), (3, 3))
        env.fake.entries.append(c)  # it comes back
        env.sync.run()
        self.assertIsNone(env.store.query("SELECT gone_at FROM catalog_items WHERE bazarr_id = '7103'")[0]["gone_at"])

    def test_with_the_policy_off_everywhere_it_does_not_look_at_the_library(self):
        env = self.env()
        env.episode(1)
        env.sync.run()
        with patch.object(env.store, "catalog_entries") as looked:
            self.assertEqual(env.auto.run(), AutoRequested())
        looked.assert_not_called()

    def test_the_labels_of_a_page_are_the_same_with_the_jobs_read_once(self):
        env = self.env()
        for n in (1, 2, 3, 4):
            env.episode(n)
        env.sync.run()
        env.translated("7101")
        env.actions.request({"items": env.ids("7102", "7103")})
        failed = env.queue.jobs.by_state("queued")[0]["job_id"]
        env.queue.jobs.transition(failed, "running")
        env.queue.jobs.transition(failed, "failed", error="boom")
        page = env.view.series_page(env.item("7", "series").id)
        by_item = {i["bazarr_id"]: i["targets"][0] for i in page["items"]}
        series = env.item("7", "series")
        for bazarr_id, indexed in by_item.items():
            entry = env.item(bazarr_id)
            plain = env.view.item(entry, series, ["vi"], env.store.decisions(), [{"code": "en", "language": "English"}])["targets"][0]
            self.assertEqual({k: indexed[k] for k in ("content", "job", "wait", "stale")}, {k: plain[k] for k in ("content", "job", "wait", "stale")}, bazarr_id)
        self.assertEqual({b: t["job"] for b, t in by_item.items()}, {"7101": "", "7102": "failed", "7103": "requested", "7104": ""})
        self.assertIsInstance(env.view.job_index([]), JobIndex)

    def test_a_job_index_for_a_page_is_read_in_a_few_queries_not_one_per_item(self):
        env = self.env()
        for n in range(1, 31):
            env.episode(n)
        env.sync.run()
        env.actions.request({"series": env.item("7", "series").id})
        calls = []
        real = env.store.query
        env.store.query = lambda sql, args=(): (calls.append(sql), real(sql, args))[1]
        try:
            env.view.series_page(env.item("7", "series").id)
        finally:
            env.store.query = real
        self.assertLess(len([c for c in calls if "FROM jobs" in c]), 4)


class PageLabelTests(CatalogCase):
    """R10-9 (the rest, W6): the labels of a page read the history of its files in a few queries and do not read a file
    again while it is unchanged, and are the labels the item would get one at a time."""

    def build(self):
        env = self.env()
        for n in range(1, 7):
            env.episode(n, vi=n != 1)
        env.sync.run()
        env.translated("7102")
        Path(env.paths("7103")[1]).write_text(SRT, encoding="utf-8")  # made by nobody here
        env.translated("7104", edited=True)
        unit_id, _ = env.translated("7105")
        env.store.add_correction(unit_id, "vi", "term", {"source": "Hi", "target": "Chao", "locked": True})  # stale
        _, target = env.translated("7106")
        env.versions.remove(target)
        env.replace_entry(env.fake.entries[6], subtitles=env.fake.entries[6].subtitles[:1], missing=("vi",))
        env.clock.advance(60)
        env.sync.run()
        return env

    def page(self, env):
        return env.view.series_page(env.item("7", "series").id)

    def test_the_page_says_what_each_item_says_on_its_own(self):
        env = self.build()
        series = env.item("7", "series")
        by_item = {i["bazarr_id"]: i["targets"][0] for i in self.page(env)["items"]}
        self.assertEqual({b: t["content"] for b, t in by_item.items()}, {
            "7101": "missing", "7102": "translated", "7103": "external", "7104": "edited", "7105": "translated",
            "7106": "missing",
        })
        self.assertEqual([b for b, t in by_item.items() if t["stale"]], ["7105"])
        self.assertEqual([b for b, t in by_item.items() if t["removed"]], ["7106"])
        for bazarr_id, indexed in by_item.items():
            plain = env.view.item(env.item(bazarr_id), series, ["vi"], env.store.decisions(), [EN])["targets"][0]
            self.assertEqual(indexed, plain, bazarr_id)

    def test_the_history_of_a_page_is_read_in_a_few_queries(self):
        env = self.build()
        for n in range(7, 31):
            env.episode(n, vi=True)
        env.sync.run()
        statements = []
        env.store._db.set_trace_callback(statements.append)
        try:
            self.page(env)
        finally:
            env.store._db.set_trace_callback(None)
        reads = [s for s in statements if "FROM translations" in s]
        self.assertTrue(1 <= len(reads) < 4, len(reads))

    def test_a_file_is_not_read_again_while_it_is_unchanged_and_is_when_it_changes(self):
        env = self.build()
        reads = []
        real = versions_module.sha256_file
        with patch.object(versions_module, "sha256_file", lambda path: (reads.append(Path(path).name), real(path))[1]):
            first = self.page(env)
            self.assertGreaterEqual(len(reads), 3)  # the files the system made are checked once
            reads.clear()
            again = self.page(env)
            self.assertEqual(reads, [])
            self.assertEqual(again["items"], first["items"])
            target = Path(env.paths("7102")[1])
            target.write_text(SRT + "\n99\n00:00:09,000 --> 00:00:10,000\nBye\n", encoding="utf-8")  # a hand edit
            changed = {i["bazarr_id"]: i["targets"][0]["content"] for i in self.page(env)["items"]}
        self.assertEqual(reads, [target.name])
        self.assertEqual(changed["7102"], "external")

    def test_what_decides_an_overwrite_still_reads_the_file(self):
        env = self.build()
        target = env.paths("7102")[1]
        self.assertTrue(env.versions.is_system_file(target))
        with patch.object(versions_module, "sha256_file", wraps=versions_module.sha256_file) as hashed:
            env.versions.is_system_file(target)
            env.versions.is_system_file(target)
        self.assertEqual(hashed.call_count, 2)


class ReadsWriteNothingTests(CatalogCase):
    """W0 lock for the Library: a GET never writes (counted on the connection, not guessed from the code)."""

    def test_the_catalog_pages_write_nothing(self):
        env = self.env(auto_request=True)
        env.episode(1)
        env.episode(2, vi=True)
        env.sync.run()
        env.actions.request({"items": env.ids("7101")})
        reads = (
            env.actions.status, env.actions.overview, env.actions.movies_page,
            lambda: env.actions.series_page(env.item("7", "series").id),
        )
        before = env.store._db.total_changes
        for read in reads:
            read()
            read()
        self.assertEqual(env.store._db.total_changes, before)


if __name__ == "__main__":
    unittest.main()
