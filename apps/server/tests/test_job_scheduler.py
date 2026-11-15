"""Jobs as rows: scheduling, leases, waiting instead of failing, the breaker (SPEC criterion 14, P8).

Everything runs on SQLite with a fake clock; no network and no real provider. ``execute`` callbacks stand in for
the translation and raise the exceptions ``TranslationAttempt`` maps provider failures to.
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path

from subserver import queue_policy as policy
from subserver.jobs import DONE, FAILED, QUEUED, RUNNING, WAITING, job_key
from subserver.queue import JobQueue
from subserver.store import Store
from tests.queue_harness import START, SchedulerCase
from subserver.translation import (
    CallBudgetError, ContextGapError, DailyQuotaExceededError, HardJobError, ModelNotFoundError, ProviderAuthError,
    ProviderUnavailableError,
)


class OrderTests(SchedulerCase):
    def test_14a_classes_run_interactive_then_retranslate_then_auto(self):
        self.add("auto", created_at=START - 10)
        self.add("redo", prio=policy.RETRANSLATE, created_at=START - 5)
        self.add("now", prio=policy.INTERACTIVE, created_at=START)
        self.assertEqual(self.drain(), ["now", "redo", "auto"])

    def test_14a_two_series_take_turns(self):
        for number in range(1, 4):
            self.add(f"a{number}", series="A", created_at=START + number)
        for number in range(1, 3):
            self.add(f"b{number}", series="B", created_at=START + 10 + number)
        self.assertEqual(self.drain(), ["a1", "b1", "a2", "b2", "a3"])

    def test_14a_aging_lifts_an_old_job_over_a_newer_class_but_never_over_interactive(self):
        old = self.add("old", created_at=START - 13 * 3600)  # two aging steps: 0 + 100
        self.add("redo", prio=policy.RETRANSLATE, created_at=START)
        self.add("click", prio=policy.INTERACTIVE, created_at=START)
        self.assertEqual(self.drain(), ["click", old, "redo"])

    def test_pure_ordering_is_stable_and_explainable(self):
        cands = [policy.Candidate(1, 0, 5.0, "A"), policy.Candidate(2, 0, 4.0, "A"), policy.Candidate(3, 0, 6.0, "B")]
        ordered = policy.order_candidates(cands, {"A": 99.0}, now=10.0)
        self.assertEqual([c.id for c in ordered], [3, 2, 1])  # B was not served yet, A just was
        self.assertEqual(policy.priority_class(200), "interactive")
        self.assertEqual(policy.effective_priority(0, 0.0, 6 * 3600 * 100), policy.AGING_CAP)


class ClaimTests(unittest.TestCase):
    def test_14b_two_connections_never_claim_the_same_job(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)  # runs after the stores are closed (cleanups run last-in first-out)
        path = Path(directory.name) / "jobs.db"
        first, second = Store(path), Store(path)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        queues = [JobQueue(first), JobQueue(second)]
        for queue in queues:
            queue.configure({}, ("light",), "gemini")
        for number in range(40):
            subtitle = f"/m/{number}.en.srt"
            queues[0].jobs.add({"subtitle_path": subtitle, "output_path": f"/m/{number}.vi.srt", "target_code": "vi"},
                               key=job_key(subtitle, f"/m/{number}.vi.srt", "vi"), job_id=f"j{number}")
        ran, lock = [], threading.Lock()

        def work(queue):
            def execute(job, update_status):
                with lock:
                    ran.append(job["job_id"])
                return "translated"

            while queue.process_once(execute):
                pass

        threads = [threading.Thread(target=work, args=(queue,)) for queue in queues]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(len(ran), 40)
        self.assertEqual(len(set(ran)), 40)
        self.assertEqual(queues[0].jobs.counts()["done"], 40)

    def test_one_open_job_per_file(self):
        queue = JobQueue(Store(":memory:"))
        key = job_key("/m/a.en.srt", "/m/a.vi.srt", "vi")
        payload = {"subtitle_path": "/m/a.en.srt", "output_path": "/m/a.vi.srt", "target_code": "vi"}
        first = queue.jobs.add(payload, key=key)
        second = queue.jobs.add(payload, key=key, prio=policy.INTERACTIVE)
        self.assertIsNone(second.job_id)
        self.assertEqual(second.existing, first.job_id)
        self.assertEqual(queue.jobs.get(first.job_id)["prio"], policy.INTERACTIVE)  # the second request lifted it


class LeaseTests(SchedulerCase):
    def interrupt(self, job, update_status):
        update_status("Sending batches")
        raise KeyboardInterrupt()

    def test_14c_a_dead_worker_returns_the_job_and_too_many_deaths_fail_it(self):
        name = self.add()
        for crash in (1, 2):
            with self.assertRaises(KeyboardInterrupt):
                self.queue.process_once(self.interrupt)
            self.assertEqual(self.state(name), RUNNING)
            restarted = JobQueue(self.store, clock=self.clock.time, monotonic=self.clock.monotonic)
            restarted.configure({}, self.MODELS, "gemini")
            self.assertEqual(restarted.recover(), 1)  # explicit, at start-up
            self.assertEqual(self.state(name), QUEUED)
            self.assertEqual(self.job(name)["attempts"], crash)
            self.assertEqual(self.job(name)["stage"], "Recovered after service restart")
        with self.assertRaises(KeyboardInterrupt):
            self.queue.process_once(self.interrupt)
        self.queue.recover()
        self.assertEqual(self.state(name), FAILED)
        self.assertIn("crashed", self.job(name)["error"])

    def test_making_a_queue_recovers_nothing(self):
        name = self.add()
        with self.assertRaises(KeyboardInterrupt):
            self.queue.process_once(self.interrupt)
        JobQueue(self.store)  # a handle, not a start-up
        self.assertEqual(self.state(name), RUNNING)

    def test_a_lease_that_runs_out_returns_the_job_without_a_restart(self):
        name = self.add()
        with self.assertRaises(KeyboardInterrupt):
            self.queue.process_once(self.interrupt)
        self.assertEqual(self.tick(), None)  # the lease (600 s) is still good: the row is not ours to take
        self.clock.advance(601)
        self.assertEqual(self.tick(), name)  # swept, queued with attempts + 1, and run again
        self.assertEqual(self.job(name)["attempts"], 1)
        self.assertEqual(self.state(name), DONE)

    def test_the_admin_can_stop_a_running_job_at_a_batch_boundary(self):
        name = self.add()

        def execute(job, update_status):
            update_status("Translating 1/3 cues")
            self.assertEqual(self.queue.stop(name), "stopping")
            update_status("Translating 2/3 cues")  # the next boundary: the stop is noticed
            return "translated"

        self.tick(execute)
        self.assertEqual(self.state(name), "cancelled")

    def test_a_waiting_job_is_stopped_at_once(self):
        name = self.add()
        self.tick(lambda job, update: (_ for _ in ()).throw(ProviderUnavailableError("503")))
        self.assertEqual(self.state(name), WAITING)
        self.assertEqual(self.queue.stop(name), "cancelled")
        self.assertEqual(self.state(name), "cancelled")


class WaitingTests(SchedulerCase):
    def raising(self, exc, calls=None):
        def execute(job, update_status):
            if calls is not None:
                calls.append(job["job_id"])
            raise exc
        return execute

    def test_14d_a_spent_daily_quota_makes_jobs_wait_for_the_reset_and_none_fail(self):
        first, second = self.add(), self.add()
        calls = []
        self.tick(self.raising(DailyQuotaExceededError("429 daily quota"), calls))
        reset = policy.daily_quota_reset_at(self.clock.now)
        self.assertEqual((self.state(first), self.job(first)["wait_reason"], self.job(first)["not_before"]), (WAITING, "quota", reset))
        self.assertIsNone(self.tick(self.raising(AssertionError("no call while the quota is spent"), calls), advance=60))
        self.assertEqual((self.state(second), self.job(second)["wait_reason"]), (WAITING, "quota"))  # shown, not failed
        self.assertEqual(self.queue.jobs.counts()["failed"], 0)
        self.assertEqual(self.queue.quota_pause_until(), reset)
        self.assertEqual(len(calls), 1)
        self.clock.advance(reset - self.clock.now + 5)  # past the reset
        order = self.drain()
        self.assertEqual(sorted(order), sorted([first, second]))
        self.assertEqual(self.queue.jobs.counts()["done"], 2)

    def test_14d_the_reset_is_pacific_midnight_not_a_fixed_day(self):
        zone = policy.zone_for("America/Los_Angeles")
        reset = policy.daily_quota_reset_at(START, zone)
        from datetime import datetime
        local = datetime.fromtimestamp(reset, zone)
        self.assertEqual((local.hour, local.minute), (0, 0))
        self.assertTrue(0 < reset - START <= 86_400 + 3_600)

    def test_quota_on_the_light_model_moves_the_job_to_the_strong_one(self):
        self.configure(models=("light", "strong"))
        name = self.add()
        models = []

        def execute(job, update_status):
            models.append(job["gst_model"])
            if job["gst_model"] == "light":
                raise DailyQuotaExceededError("429")
            return "translated"

        self.drain(execute)
        self.assertEqual(models, ["light", "strong"])
        self.assertEqual(self.state(name), DONE)
        self.assertEqual(self.queue.quota_pause_until(), None)  # the strong model still has quota

    def test_14e_the_breaker_opens_after_the_threshold_rests_then_allows_one_trial(self):
        name = self.add()
        calls = []
        failing = self.raising(ProviderUnavailableError("503"), calls)
        self.tick(failing)  # failure 1: below the threshold, the job tries again shortly
        self.assertEqual((self.state(name), self.job(name)["wait_reason"]), (WAITING, "rate"))
        self.tick(failing, advance=31)  # 2
        self.tick(failing, advance=31)  # 3: the breaker opens
        health = self.queue.jobs.health_map()[("gemini", "light")]
        self.assertEqual((health.state, health.until), (policy.OPEN, self.clock.now + 300))
        self.assertEqual((self.state(name), self.job(name)["wait_reason"]), (WAITING, "provider"))
        self.assertEqual(len(calls), 3)

        self.assertIsNone(self.tick(failing, advance=299))  # resting: no call at all
        self.assertEqual(len(calls), 3)

        nested = []

        def trial(job, update_status):
            calls.append(job["job_id"])
            nested.append(self.queue.jobs.health_map()[("gemini", "light")].state)
            nested.append(self.queue.process_once(lambda j, u: "translated"))  # nobody else may use the model now
            raise ProviderUnavailableError("503")

        self.tick(trial, advance=2)  # the rest is over: exactly one trial call, and it fails
        self.assertEqual(nested, [policy.HALF_OPEN, False])
        health = self.queue.jobs.health_map()[("gemini", "light")]
        self.assertEqual((health.state, health.until), (policy.OPEN, self.clock.now + 900))  # a longer rest
        self.assertIsNone(self.tick(failing, advance=899))
        self.tick(None, advance=2)  # the trial succeeds: closed again
        self.assertEqual(self.queue.jobs.health_map()[("gemini", "light")].state, policy.CLOSED)
        self.assertEqual(self.state(name), DONE)
        self.assertEqual(self.queue.jobs.counts()["failed"], 0)

    def test_the_breaker_gives_the_job_to_the_strong_model_when_it_opens(self):
        self.configure(models=("light", "strong"), breaker_threshold=1)
        name = self.add()
        seen = []

        def execute(job, update_status):
            seen.append(job["gst_model"])
            if job["gst_model"] == "light":
                raise ProviderUnavailableError("503")
            return "translated"

        self.drain(execute)
        self.assertEqual(seen, ["light", "strong"])
        self.assertEqual(self.state(name), DONE)

    def test_14f_a_refused_key_parks_the_jobs_until_a_working_key_is_set(self):
        keys = {"ok": False}
        queue = JobQueue(self.store, clock=self.clock.time, monotonic=self.clock.monotonic, key_ok=lambda: keys["ok"])
        queue.configure({}, ("light",), "gemini")
        self.queue = queue
        first, second = self.add(), self.add()
        self.tick(self.raising(ProviderAuthError("401 bad key")))
        whole = self.queue.jobs.health_map()[("gemini", policy.ALL_MODELS)]
        self.assertEqual(whole.state, policy.INVALID)
        self.assertEqual((self.state(first), self.job(first)["wait_reason"]), (WAITING, "provider"))
        calls = []
        self.assertIsNone(self.tick(self.raising(AssertionError("no call with a refused key"), calls), advance=3600))
        self.assertEqual((self.state(second), self.job(second)["wait_reason"]), (WAITING, "provider"))
        self.assertEqual(self.queue.health_report()[0]["state"], policy.INVALID)  # what Settings shows (FR-17.7)
        keys["ok"] = True  # the admin pasted a new key and it tested fine
        self.assertEqual(sorted(self.drain()), sorted([first, second]))
        self.assertEqual(self.queue.jobs.counts()["failed"], 0)

    def test_an_unknown_model_waits_like_a_refused_key_until_the_setting_changes(self):
        name = self.add()
        self.tick(self.raising(ModelNotFoundError("404", model="light")))
        self.assertEqual((self.state(name), self.job(name)["wait_reason"]), (WAITING, "provider"))
        self.assertIsNone(self.tick(advance=60))
        self.configure(models=("light-2",))  # the admin fixed the model name
        self.assertEqual(self.drain(), [name])

    def test_14g_a_job_that_needs_the_strong_model_does_not_block_the_others(self):
        self.configure(models=("light", "strong"))
        strong = self.add("needs-strong", prio=policy.INTERACTIVE)
        self.queue.jobs.transition(strong, QUEUED, tier="strong")
        other = self.add("light-job")
        self.queue.jobs.set_health("gemini", "strong", policy.after_quota(policy.Health(), self.clock.now + 7200))
        self.assertEqual(self.tick(), other)
        self.assertEqual((self.state(strong), self.job(strong)["wait_reason"]), (WAITING, "quota"))

    def test_14h_a_setting_changed_mid_job_does_not_change_that_job(self):
        self.configure(models=("model-a",), gst_temperature="0.7")
        name = self.add()
        seen = {}

        def execute(job, update_status):
            seen["model"] = job["gst_model"]
            self.configure(models=("model-b",), gst_temperature="0.1")
            update_status("Translating")
            return "translated"

        self.tick(execute)
        job = self.job(name)
        self.assertEqual((seen["model"], job["gst_model"]), ("model-a", "model-a"))
        self.assertEqual(job["snapshot"]["model"], "model-a")
        self.assertEqual(job["snapshot"]["settings"]["gst_temperature"], "0.7")
        row = self.store.query("SELECT snapshot_json FROM jobs WHERE job_id = ?", (name,))[0]
        self.assertIn("model-a", row["snapshot_json"])

    def test_14i_a_gap_waits_visibly_and_names_the_missing_episode(self):
        name = self.add()
        error = ContextGapError("S01E01 has no source subtitle", retry_at=self.clock.now + 1800, since=self.clock.now,
                                missing=["S01E01"])
        self.tick(self.raising(error))
        job = self.job(name)
        self.assertEqual((job["state"], job["wait_reason"], job["missing_units"]), (WAITING, "context", ["S01E01"]))
        self.assertEqual(job["gap_since"], self.clock.now)
        self.assertIsNone(self.tick(advance=1799))
        self.assertEqual(self.tick(advance=2), name)

    def test_14j_a_telemetry_that_raises_never_fails_the_job(self):
        class Broken:
            def event(self, *args, **kwargs):
                raise RuntimeError("the log is full")

        self.queue.telemetry = Broken()
        name = self.add()
        self.tick()
        self.assertEqual(self.state(name), DONE)

    def test_14l_the_call_cap_of_a_job_fails_it_with_a_clear_reason(self):
        name = self.add()
        self.tick(self.raising(CallBudgetError("the job already made 20 model calls (max_calls_per_job=20)", "job")))
        job = self.job(name)
        self.assertEqual(job["state"], FAILED)
        self.assertIn("max_calls_per_job", job["error"])

    def test_the_day_cap_makes_the_job_wait_for_tomorrow_not_fail(self):
        name = self.add()
        self.tick(self.raising(CallBudgetError("the day's 100 model calls are spent", "day")))
        job = self.job(name)
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "quota"))
        self.assertEqual(job["not_before"], policy.next_utc_midnight(self.clock.now))

    def test_a_hard_job_goes_to_the_strong_model_once_then_fails(self):
        self.configure(models=("light", "strong"))
        name = self.add()
        models = []

        def execute(job, update_status):
            models.append(job["gst_model"])
            raise HardJobError("3 cue(s) failed validation after repair")

        self.drain(execute)
        self.assertEqual(models, ["light", "strong"])
        self.assertEqual(self.state(name), FAILED)
        self.assertEqual(self.job(name)["escalated_from"], "light")

    def test_14k_a_blocked_job_is_escalated_and_fails_with_the_reason(self):
        self.configure(models=("light", "strong"))
        name = self.add()

        def execute(job, update_status):
            error = HardJobError("2 cue(s) failed validation after repair")
            error.blocked = 4
            raise error

        self.drain(execute)
        job = self.job(name)
        self.assertEqual(job["state"], FAILED)
        self.assertIn("blocked", job["error"])

    def test_a_settle_window_makes_the_job_wait_with_its_reason(self):
        subtitle = Path(tempfile.mkdtemp()) / "Movie.en.srt"
        subtitle.write_text("x", encoding="utf-8")
        os.utime(subtitle, (self.clock.now - 10, self.clock.now - 10))
        name = self.add(subtitle_path=str(subtitle))
        self.queue.process_once(lambda j, u: "translated", settle_seconds=120)
        job = self.job(name)
        self.assertEqual((job["state"], job["wait_reason"]), (WAITING, "settle"))
        self.assertEqual(job["not_before"], self.clock.now - 10 + 120)
        self.clock.advance(111)
        self.assertTrue(self.queue.process_once(lambda j, u: "translated", settle_seconds=120))
        self.assertEqual(self.state(name), DONE)


class PolicyTests(unittest.TestCase):
    def test_the_breaker_is_a_pure_state_machine(self):
        h = policy.Health()
        h = policy.after_failure(h, 100, 3)
        h = policy.after_failure(h, 110, 3)
        self.assertEqual((h.state, h.failures), (policy.CLOSED, 2))
        h = policy.after_failure(h, 120, 3)
        self.assertEqual((h.state, h.until), (policy.OPEN, 420))
        self.assertFalse(policy.health_usable(h, 419))
        self.assertTrue(policy.health_usable(h, 420))
        half = policy.begin_trial(h, 420)
        self.assertEqual(half.state, policy.HALF_OPEN)
        self.assertFalse(policy.health_usable(half, 421))
        again = policy.after_failure(half, 430, 3)
        self.assertEqual((again.state, again.until, again.level), (policy.OPEN, 430 + 900, 1))
        self.assertEqual(policy.after_success(again), policy.Health())

    def test_rests_grow_to_the_last_step_and_stay_there(self):
        h = policy.Health(policy.HALF_OPEN, 3, 0, 0.0)
        untils = []
        for _ in range(6):
            h = policy.after_failure(policy.begin_trial(policy.replace(h, until=0.0, state=policy.OPEN), 0.0), 0.0, 3)
            untils.append(h.until)
        self.assertEqual(untils[:4], [900, 1800, 3600, 3600])

    def test_a_refused_key_stays_refused_until_changed(self):
        h = policy.after_invalid(policy.Health(), "auth")
        self.assertFalse(policy.health_usable(h, 10**12))
        self.assertEqual(policy.wait_reason_of(h), "provider")
        self.assertEqual(policy.wait_reason_of(policy.after_quota(policy.Health(), 5)), "quota")

    def test_the_strong_tier_needs_the_second_model(self):
        models = ("light", "strong")
        self.assertEqual(policy.pick_model(models, {"strong"}, "strong"), None)
        self.assertEqual(policy.pick_model(models, {"strong"}, ""), "light")
        self.assertEqual(policy.pick_model(models, {"light"}, ""), "strong")
        self.assertEqual(policy.pick_model(("only",), set(), "strong"), "only")


if __name__ == "__main__":
    unittest.main()
