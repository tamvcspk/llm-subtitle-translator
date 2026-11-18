"""Follow-ups of P14 found on the dev server: Extract facts for the episodes the admin picks, run in the background (the
box can be closed and Activity shows how far it is), and the facts of an episode shown on the console. Every provider is a
fake; nothing touches the network."""

import threading
import unittest

from subserver import library_api, suggestion_api, tasks_api
from subserver.background import CANCELLED, DONE, FAILED, QUEUED, BackgroundTasks, Cancelled

from .test_proposals import Env as ProposalEnv
from .test_story_memory import all_sources


class BackgroundTasksTests(unittest.TestCase):
    def test_a_task_runs_after_the_answer_and_says_how_far_it_is(self):
        tasks = BackgroundTasks()
        gate = threading.Event()

        def work(progress):
            progress(1, 3, "S01E02")
            gate.wait(5)
            progress(3, 3)
            return {"read": 3}

        task = tasks.submit("extract-facts", "Extract facts · Show", work, series_id=7)
        self.assertIn(task["state"], (QUEUED, "running"))
        gate.set()
        self.assertTrue(tasks.wait_idle())
        done = tasks.get(task["id"])
        self.assertEqual((done["state"], done["done"], done["total"], done["result"]), (DONE, 3, 3, {"read": 3}))
        self.assertEqual(done["series_id"], 7)

    def test_a_task_that_fails_is_listed_with_its_error_and_never_takes_the_thread_down(self):
        tasks = BackgroundTasks(redact=lambda text: text.replace("SECRET", "***"))

        def boom(progress):
            raise RuntimeError("the key SECRET was refused")

        failed = tasks.submit("suggest", "Look", boom)
        self.assertTrue(tasks.wait_idle())
        self.assertEqual(tasks.get(failed["id"])["state"], FAILED)
        self.assertEqual(tasks.get(failed["id"])["error"], "RuntimeError: the key *** was refused")
        later = tasks.submit("suggest", "Again", lambda progress: {"ok": True})
        self.assertTrue(tasks.wait_idle())
        self.assertEqual(tasks.get(later["id"])["state"], DONE)  # the thread is still there

    def test_a_message_written_for_the_admin_is_shown_as_it_is(self):
        tasks = BackgroundTasks()

        def refuse(progress):
            raise ValueError("The provider could not finish (QuotaExhausted: spent)")

        task = tasks.submit("extract-facts", "Extract", refuse)
        tasks.wait_idle()
        self.assertEqual(tasks.get(task["id"])["error"], "The provider could not finish (QuotaExhausted: spent)")

    def test_tasks_run_one_at_a_time_in_the_order_they_came(self):
        tasks, order, gate = BackgroundTasks(), [], threading.Event()
        tasks.submit("a", "first", lambda progress: (gate.wait(5), order.append("first")) and None)
        second = tasks.submit("a", "second", lambda progress: order.append("second"))
        listed = tasks.tasks()
        self.assertEqual([t["title"] for t in listed], ["first", "second"])
        self.assertEqual(second["state"], QUEUED)
        gate.set()
        tasks.wait_idle()
        self.assertEqual(order, ["first", "second"])

    def test_a_queued_task_is_stopped_before_it_starts(self):
        tasks, ran, gate = BackgroundTasks(), [], threading.Event()
        tasks.submit("a", "busy", lambda progress: gate.wait(5) and None)
        waiting = tasks.submit("a", "waiting", lambda progress: ran.append(True))
        self.assertEqual(tasks.cancel(waiting["id"])["state"], CANCELLED)
        gate.set()
        tasks.wait_idle()
        self.assertEqual(ran, [])

    def test_a_running_task_stops_at_its_next_item(self):
        tasks, started, gate, seen = BackgroundTasks(), threading.Event(), threading.Event(), []

        def work(progress):
            for n in range(5):
                progress(n, 5)
                seen.append(n)
                started.set()
                gate.wait(5)
                gate.clear()
            return {}

        task = tasks.submit("a", "long", work)
        started.wait(5)
        tasks.cancel(task["id"])
        gate.set()
        tasks.wait_idle()
        self.assertEqual((tasks.get(task["id"])["state"], seen), (CANCELLED, [0]))

    def test_finished_tasks_are_kept_up_to_a_limit_and_an_unknown_one_is_not_found(self):
        tasks = BackgroundTasks(keep=3)
        ids = [tasks.submit("a", f"t{n}", lambda progress: {}) ["id"] for n in range(6)]
        tasks.wait_idle()
        self.assertLessEqual(len(tasks.tasks()), 3 + 1)
        with self.assertRaises(LookupError):
            tasks.get(ids[0])
        with self.assertRaises(LookupError):
            tasks.cancel("0" * 12)

    def test_routes(self):
        tasks = BackgroundTasks()
        task = tasks.submit("a", "t", lambda progress: {"ok": True})
        tasks.wait_idle()
        status, payload = tasks_api.handle(tasks, "GET", "/api/tasks", {})
        self.assertEqual((status, [t["id"] for t in payload["items"]]), (200, [task["id"]]))
        self.assertEqual(tasks_api.handle(tasks, "GET", f"/api/tasks/{task['id']}", {})[1]["state"], DONE)
        self.assertEqual(tasks_api.handle(tasks, "POST", f"/api/tasks/{task['id']}/cancel", {})[0], 200)
        self.assertEqual(tasks_api.handle(tasks, "GET", "/api/tasks/" + "f" * 12, {})[0], 404)
        self.assertIsNone(tasks_api.handle(tasks, "DELETE", "/api/tasks", {}))
        self.assertTrue(tasks_api.handles("/api/tasks/abc"))
        self.assertIsInstance(Cancelled(), Exception)


def with_episodes(test):
    """Six episodes with a source subtitle, all known to the library; only episode 1's facts are stored."""
    env = all_sources(test)
    env.translate(1)
    env.service.tasks = BackgroundTasks()
    return env


class PickedEpisodesTests(unittest.TestCase):
    def test_only_the_episodes_picked_are_read_and_not_the_ones_before_them(self):
        env = with_episodes(self)
        before = len(env.calls("extract"))
        result = env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(5), env.unit_id(3)]})
        self.assertEqual(result["extracted"], ["S01E03", "S01E05"])  # story order; S01E02 and S01E04 are left alone
        self.assertEqual(len(env.calls("extract")), before + 2)
        coverage = {u["label"]: u["has_facts"] for u in env.service.series_memory(env.store.list_series()[0].id)["units"]}
        self.assertEqual([k for k, v in coverage.items() if v], ["S01E01", "S01E03", "S01E05"])

    def test_without_a_pick_it_reads_the_episode_and_every_earlier_one_as_before(self):
        env = with_episodes(self)
        self.assertEqual(env.service.refresh_context(env.unit_id(3))["extracted"], ["S01E02", "S01E03"])

    def test_a_bad_pick_is_refused_before_any_call(self):
        env = with_episodes(self)
        before = len(env.fake.calls)
        for bad in ([], "S01E02", [True], ["3"], [999_999]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                env.service.refresh_context(env.unit_id(1), {"unit_ids": bad})
        self.assertEqual(len(env.fake.calls), before)

    def test_in_the_background_it_answers_at_once_and_activity_follows_it(self):
        env = with_episodes(self)
        answer = env.service.refresh_context(
            env.unit_id(1), {"unit_ids": [env.unit_id(2), env.unit_id(4)], "background": True},
        )
        self.assertEqual(answer["task"]["kind"], "extract-facts")
        self.assertIn("2 episodes", answer["task"]["title"])
        self.assertTrue(env.service.tasks.wait_idle())
        (task,) = env.service.tasks.tasks()
        self.assertEqual((task["state"], task["done"], task["total"]), (DONE, 2, 2))
        self.assertEqual(task["result"]["extracted"], ["S01E02", "S01E04"])

    def test_stopping_it_keeps_what_was_read_and_reads_no_more(self):
        env = with_episodes(self)
        tasks, handler = env.service.tasks, env.fake.handler

        def stop_after_the_first(req, call_no):
            if req.task == "extract":
                tasks.cancel(tasks.tasks()[0]["id"])
            return handler(req, call_no)

        env.fake.handler = stop_after_the_first
        env.service.refresh_context(
            env.unit_id(1), {"unit_ids": [env.unit_id(2), env.unit_id(3), env.unit_id(4)], "background": True},
        )
        tasks.wait_idle()
        (task,) = tasks.tasks()
        self.assertEqual(task["state"], CANCELLED)
        coverage = {u["label"]: u["has_facts"] for u in env.service.series_memory(env.store.list_series()[0].id)["units"]}
        self.assertEqual([k for k, v in coverage.items() if v], ["S01E01", "S01E02"])  # the one read stays stored

    def test_a_provider_that_gives_up_is_a_failed_task_with_a_clear_message(self):
        from subcore.providers import QuotaExhausted

        env = with_episodes(self)

        def refuse(req, n):
            raise QuotaExhausted("daily quota")

        env.fake.handler = refuse
        env.service.refresh_context(env.unit_id(1), {"unit_ids": [env.unit_id(2)], "background": True})
        env.service.tasks.wait_idle()
        (task,) = env.service.tasks.tasks()
        self.assertEqual(task["state"], FAILED)
        self.assertIn("QuotaExhausted", task["error"])

    def test_without_background_work_wired_it_says_so(self):
        env = all_sources(self)
        env.translate(1)
        with self.assertRaisesRegex(ValueError, "background"):
            env.service.refresh_context(env.unit_id(1), {"background": True})


class FactsOfAnEpisodeTests(unittest.TestCase):
    def test_what_the_facts_say_can_be_read_and_nothing_is_asked_of_the_model(self):
        env = with_episodes(self)
        calls = len(env.fake.calls)
        read = env.service.unit_facts(env.unit_id(1))
        self.assertEqual((read["label"], read["stored"], read["has_source"]), ("S01E01", True, True))
        self.assertEqual([c["name"] for c in read["facts"]["characters"]], ["Alice", "Bob"])
        self.assertIn("EP1-SUMMARY-MARK", read["facts"]["summary"])
        self.assertEqual(read["facts"]["relationship_events"][0]["evidence"], "EP1-LOVERS-MARK")
        none = env.service.unit_facts(env.unit_id(4))
        self.assertEqual((none["stored"], none["facts"]), (False, None))
        self.assertEqual(len(env.fake.calls), calls)

    def test_routes(self):
        env = with_episodes(self)
        handle = lambda method, path, body=None: library_api.handle(env.service, method, path, {}, body)  # noqa: E731
        status, payload = handle("GET", f"/api/units/{env.unit_id(1)}/facts")
        self.assertEqual((status, payload["stored"]), (200, True))
        self.assertEqual(handle("GET", "/api/units/999999/facts")[0], 404)
        status, payload = handle(
            "POST", f"/api/units/{env.unit_id(1)}/context/extract", {"unit_ids": [env.unit_id(2)], "background": True},
        )
        self.assertEqual((status, payload["task"]["kind"]), (200, "extract-facts"))
        self.assertEqual(handle("POST", f"/api/units/{env.unit_id(1)}/context/extract", {"unit_ids": []})[0], 400)
        env.service.tasks.wait_idle()


class ProposalsInTheBackgroundTests(unittest.TestCase):
    def test_looking_for_proposals_can_run_after_the_answer(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        env.suggestions.tasks = tasks = BackgroundTasks()
        status, payload = suggestion_api.handle(
            env.suggestions, "POST", "/api/suggestions/generate", {},
            {"series_id": env.series_id, "lang": "vi", "background": True},
        )
        self.assertEqual((status, payload["task"]["kind"]), (200, "suggest"))
        self.assertTrue(tasks.wait_idle())
        (task,) = tasks.tasks()
        self.assertEqual((task["state"], task["result"]["ok"]), (DONE, True))
        self.assertGreater(len(env.pending()), 0)

    def test_an_estimate_never_goes_to_the_background(self):
        env = ProposalEnv(self, suggest_from_facts=False)
        env.translate(6)
        env.suggestions.tasks = tasks = BackgroundTasks()
        answer = env.suggestions.generate(env.series_id, "vi", estimate_only=True, background=True)
        self.assertEqual((answer["estimate_only"], tasks.tasks()), (True, []))


if __name__ == "__main__":
    unittest.main()
