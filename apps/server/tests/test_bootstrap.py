"""P13: the bootstrap of ``auto_request`` (SPEC FR-15.4, FR-15.12; ADR-10). A series with nothing approved yet gets only
its first episodes from the policy until its glossary has been reviewed. Fake Bazarr, fake model; nothing touches the
network."""

import unittest

from subserver.services.auto_request import AutoRequest, sync_and_request
from subserver.services.bootstrap import BootstrapState, is_locked, story_order
from subserver.api.catalog_api import handle
from subserver.domain.ports import SeriesSeed
from subserver.services.suggestions import Proposal, SuggestionService

from .test_catalog import CatalogCase, Env


class Recorder:
    def __init__(self):
        self.events = []

    def event(self, kind, **data):
        self.events.append((kind, data))

    def signal(self, *args, **kwargs):
        pass

    def llm_call(self, *args, **kwargs):
        pass


class BootstrapCase(CatalogCase):
    def env(self, episodes=5, bootstrap=2, **settings):
        env = Env(self, auto_request=True, auto_request_bootstrap=bootstrap, **settings)
        env.recorder = Recorder()
        env.auto = AutoRequest(env.store, env.requests, lambda: env.settings, lambda: env.config, env.recorder, clock=env.clock.time)
        env.actions.auto = env.auto
        env.actions.spawn = lambda work: work()  # the pass after "let it go" runs in a thread; a test runs it at once
        for n in range(1, episodes + 1):
            env.episode(n)
        env.sync.run()
        env.suggestions = SuggestionService(env.store, env.library, settings_source=lambda: env.settings)
        return env

    def requested(self, env):
        return sorted(
            int(env.item(b).episode) for b in (f"71{n:02d}" for n in range(1, 10))
            if env.store.catalog_lookup("episode", b) is not None and "Requested" in env.label(b)["text"]
        )

    def series_id(self, env):
        return env.store.upsert_series(env.item("7", "series").series_key, title="Show", bazarr_id="7")

    def page(self, env):
        return env.view.series_page(env.item("7", "series").id)


class HeldBackTests(BootstrapCase):
    def test_a_series_with_nothing_approved_gets_only_its_first_episodes_from_the_policy(self):
        env = self.env()
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual(made.created, 2)
        self.assertEqual(self.requested(env), [1, 2])
        self.assertEqual({env.queue.jobs.get(j["job_id"])["origin"] for j in env.jobs_open()}, {"policy"})

    def test_the_rest_stay_Missing_with_a_readable_reason_and_no_job(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        held = next(i for i in self.page(env)["items"] if i["bazarr_id"] == "7103")
        label = held["targets"][0]
        self.assertEqual((label["content"], label["job"], label["reason"]), ("missing", "", "bootstrap"))
        self.assertEqual(label["text"], "Missing · Waiting for glossary review")
        self.assertEqual(env.label("7101")["text"], "Missing · Requested")  # an episode that may go has no such reason
        series = self.page(env)["series"]["bootstrap"]
        self.assertEqual((series["locked"], series["limit"], series["held"]), (True, 2, 3))
        self.assertEqual(len(env.jobs_open()), 2)  # no job exists for the held ones: it is a reason, not a state

    def test_it_says_so_once_in_the_event_log_and_again_only_when_the_numbers_change(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        sync_and_request(env.sync, env.auto)
        held = [data for kind, data in env.recorder.events if kind == "policy.bootstrap"]
        self.assertEqual([(d["series"], d["held"]) for d in held], [("Show", 3)])

    def test_an_admins_own_request_is_never_held_back(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        made = env.actions.request({"items": env.ids("7105")})
        self.assertEqual(made["count"], 1)
        job = env.queue.jobs.get(made["created"][0])
        self.assertEqual(job["origin"], "manual")

    def test_the_first_episodes_are_the_first_in_story_order_and_specials_come_last(self):
        env = self.env(episodes=0)
        env.episode(3)
        env.episode(1, season=0)  # a special
        env.episode(2)
        env.episode(1)
        env.sync.run()
        sync_and_request(env.sync, env.auto)
        picked = sorted((env.item(b).season, env.item(b).episode) for b in ("7103", "7001", "7102", "7101") if "Requested" in env.label(b)["text"])
        self.assertEqual(picked, [(1, 1), (1, 2)])
        order = story_order([e for e in env.store.catalog_entries("episode")])
        self.assertEqual([(e.season, e.episode) for e in order], [(1, 1), (1, 2), (1, 3), (0, 1)])

    def test_a_policy_that_is_off_for_the_series_has_nothing_to_hold_back(self):
        env = self.env()
        env.actions.set_policy(env.item("7", "series").id, {"auto_request": "off"})
        sync_and_request(env.sync, env.auto)
        self.assertEqual(env.jobs_open(), [])
        series = self.page(env)["series"]["bootstrap"]
        self.assertFalse(series["locked"])  # nothing is asking for these episodes, so nothing is "waiting"
        self.assertEqual(series["held"], 0)

    def test_the_wait_can_be_switched_off_with_zero(self):
        env = self.env(bootstrap=0)
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual(made.created, 5)


class LettingGoTests(BootstrapCase):
    def propose(self, env, *proposals):
        return env.suggestions.propose(self.series_id(env), "vi", list(proposals), source="facts")

    def test_reviewing_everything_that_waits_lets_the_rest_through_on_the_next_sync(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        self.propose(env, Proposal("term", {"source": "Kyber", "target": "Kyber"}), Proposal("address_rule", {"a": "A", "b": "B", "a_to_b": "anh"}))
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 2)  # suggestions wait: still held
        first, second = env.suggestions.listing(status="pending")["items"]
        env.suggestions.accept(first["id"])
        sync_and_request(env.sync, env.auto)
        # An approved glossary entry lets it go even though another suggestion still waits.
        self.assertEqual(len(env.jobs_open()), 5)

    def test_rejecting_all_of_them_lets_it_go_too(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        self.propose(env, Proposal("term", {"source": "Kyber", "target": "Kyber"}))
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 2)
        env.suggestions.reject(env.suggestions.listing(status="pending")["items"][0]["id"])
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 5)

    def test_some_rejected_and_some_still_waiting_keeps_it_held_until_something_is_approved(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        self.propose(env, Proposal("term", {"source": "A", "target": "a"}), Proposal("term", {"source": "B", "target": "b"}))
        env.suggestions.reject(env.suggestions.listing(status="pending")["items"][0]["id"])
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 2)
        env.suggestions.reject(env.suggestions.listing(status="pending")["items"][0]["id"])
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 5)

    def test_a_glossary_the_admin_already_wrote_means_there_is_no_wait(self):
        env = self.env()
        env.library.glossaries.write("series", env.item("7", "series").series_key, "vi", "terms:\n  - {source: Kyber, target: Kyber}\n")
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual(made.created, 5)
        self.assertFalse(self.page(env)["series"]["bootstrap"]["locked"])

    def test_a_character_card_counts_as_approved_as_well(self):
        env = self.env()
        env.store.card_save("series", env.item("7", "series").series_key, "Vi", {"name": "Vi", "speech": "blunt"})
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual(made.created, 5)

    def test_an_empty_glossary_is_not_an_approved_one(self):
        env = self.env()
        env.library.glossaries.write("series", env.item("7", "series").series_key, "vi", "")
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual(made.created, 2)

    def test_letting_it_go_asks_for_the_rest_at_once_and_holding_it_again_is_possible(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 2)
        catalog_id = env.item("7", "series").id
        self.assertEqual(env.actions.set_bootstrap(catalog_id, {}), {"ok": True, "released": True, "started": True})
        self.assertEqual(len(env.jobs_open()), 5)  # the pass for this series ran at once, not at the next sync
        self.assertEqual(self.page(env)["series"]["bootstrap"]["released"], True)
        env.actions.set_bootstrap(catalog_id, {"released": False})
        self.assertTrue(is_locked(limit=2, released=False, seed=SeriesSeed()))

    def test_it_says_started_false_when_another_pass_of_the_policy_is_running(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        catalog_id = env.item("7", "series").id
        self.assertTrue(env.auto._running.acquire(blocking=False))  # the sync's own pass holds the lock
        try:
            answer = env.actions.set_bootstrap(catalog_id, {})
        finally:
            env.auto._running.release()
        self.assertEqual(answer, {"ok": True, "released": True, "started": False})
        self.assertEqual(len(env.jobs_open()), 2)  # released, but nothing asked yet: the next sync does
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 5)

    def test_the_pass_after_letting_it_go_is_for_that_series_only(self):
        env = self.env()
        env.add_series("8", "Other", "80")
        env.episode(1, series="8")
        env.sync.run()
        sync_and_request(env.sync, env.auto)
        before = len(env.jobs_open())  # two of Show, one of Other
        env.actions.set_bootstrap(env.item("7", "series").id, {})
        opened = env.jobs_open()
        self.assertEqual(len(opened), before + 3)
        self.assertEqual({j["series_id"] for j in opened}, {"7", "8"})

    def test_the_route_validates_and_finds_the_series(self):
        env = self.env()
        catalog_id = env.item("7", "series").id
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{catalog_id}/bootstrap", {})[1], {"ok": True, "released": True, "started": True})
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{catalog_id}/bootstrap", {"released": "yes"})[0], 400)
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{catalog_id}/bootstrap", {"estimate_only": 1})[0], 400)
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{catalog_id}/bootstrap", {"released": False, "estimate_only": True})[0], 400)
        self.assertEqual(handle(env.actions, "PUT", "/api/catalog/series/999/bootstrap", {})[0], 404)
        episode = env.item("7101").id
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{episode}/bootstrap", {})[0], 404)


class WhichEpisodesTests(BootstrapCase):
    """FR-15.12 (P19d): the episodes a series is held on are the ones the policy can ask for, and the numbers agree."""

    def test_episodes_without_a_source_subtitle_do_not_take_a_place_and_are_not_held(self):
        env = self.env(episodes=0)
        env.episode(1, source=False)
        env.episode(2, source=False)
        for n in (3, 4, 5, 6):
            env.episode(n)
        env.sync.run()
        _, made = sync_and_request(env.sync, env.auto)
        self.assertEqual((made.created, self.requested(env)), (2, [3, 4]))  # not stuck on two episodes with nothing to read
        bootstrap = self.page(env)["series"]["bootstrap"]
        self.assertEqual((bootstrap["held"], bootstrap["held_by_lang"], bootstrap["no_source"]), (2, {"vi": 2}, 2))
        no_source = next(i for i in self.page(env)["items"] if i["bazarr_id"] == "7101")
        self.assertEqual(no_source["targets"][0]["reason"], "")  # Missing, with no source: not "waiting for the glossary"

    def test_an_episode_the_policy_will_not_ask_for_again_does_not_take_a_place_either(self):
        env = self.env(episodes=0)
        for n in (1, 2, 3, 4, 5):
            env.episode(n)
        env.sync.run()
        sync_and_request(env.sync, env.auto)
        first = next(j for j in env.jobs_open() if j["media_id"] == "7101")
        env.queue.jobs.transition(first["job_id"], "failed", error="boom")
        env.queue.jobs.transition(next(j for j in env.jobs_open() if j["media_id"] == "7102")["job_id"], "cancelled")
        env.actions.ignore({"items": env.ids("7103")}, True)
        sync_and_request(env.sync, env.auto)
        self.assertEqual(self.requested(env), [4, 5])  # 1 failed, 2 cancelled, 3 ignored: the first two that can be asked for
        self.assertEqual(self.page(env)["series"]["bootstrap"]["held_by_lang"], {"vi": 0})

    def test_held_by_lang_is_what_the_translate_whole_series_button_would_still_ask_for(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        bootstrap = self.page(env)["series"]["bootstrap"]
        held_ids = [i["id"] for i in self.page(env)["items"] if i["targets"][0]["reason"]]
        planned = env.actions.request({"series": env.item("7", "series").id, "estimate_only": True})
        self.assertEqual(bootstrap["held_by_lang"], {"vi": 3})
        self.assertEqual(planned["planned_total"], len(held_ids))  # the same episodes: missing, with a source, no job yet

    def test_the_series_page_says_what_the_box_needs(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        bootstrap = self.page(env)["series"]["bootstrap"]
        self.assertEqual(
            (bootstrap["glossary_empty"], bootstrap["open_jobs"], bootstrap["memory_on"], bootstrap["relockable"],
             bootstrap["queued_policy_jobs"]), (True, 2, False, False, 0),
        )


class EstimateTests(BootstrapCase):
    def test_the_estimate_changes_nothing_and_says_what_letting_it_go_would_ask_for(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        catalog_id = env.item("7", "series").id
        answer = env.actions.set_bootstrap(catalog_id, {"estimate_only": True})
        self.assertEqual((answer["ok"], answer["estimate_only"], answer["released"]), (True, True, False))
        self.assertEqual((answer["planned_total"], answer["estimate"]["jobs"], answer["glossary_empty"]), (3, 3, True))
        self.assertEqual((answer["daily_cap"], answer["cap_left"]), (0, None))
        self.assertNotIn("started", answer)
        self.assertEqual(len(env.jobs_open()), 2)
        self.assertFalse(self.page(env)["series"]["bootstrap"]["released"])

    def test_it_counts_the_policys_own_jobs_against_the_daily_cap(self):
        env = self.env(auto_request_daily_cap=3)
        sync_and_request(env.sync, env.auto)  # two jobs made today
        answer = env.actions.set_bootstrap(env.item("7", "series").id, {"estimate_only": True})
        self.assertEqual((answer["daily_cap"], answer["cap_left"]), (3, 1))

    def test_a_glossary_that_exists_is_not_a_warning(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        env.store.card_save("series", env.item("7", "series").series_key, "Vi", {"name": "Vi", "speech": "blunt"})
        catalog_id = env.item("7", "series").id
        answer = env.actions.set_bootstrap(catalog_id, {"estimate_only": True})
        self.assertEqual((answer["glossary_empty"], answer["planned_total"]), (False, 0))  # approved: nothing is held any more


class HoldAgainTests(BootstrapCase):
    def release(self, env):
        sync_and_request(env.sync, env.auto)
        env.actions.set_bootstrap(env.item("7", "series").id, {})  # five open jobs, three of them beyond the bootstrap

    def test_it_is_offered_only_when_holding_would_hold_something(self):
        env = self.env()
        self.release(env)
        bootstrap = self.page(env)["series"]["bootstrap"]
        self.assertEqual((bootstrap["released"], bootstrap["relockable"], bootstrap["queued_policy_jobs"]), (True, True, 3))
        # Suggestions were decided and none waits: the series is let go whatever is pressed, so there is nothing to hold.
        env.suggestions.propose(self.series_id(env), "vi", [Proposal("term", {"source": "A", "target": "a"})], source="facts")
        env.suggestions.reject(env.suggestions.listing(status="pending")["items"][0]["id"])
        bootstrap = self.page(env)["series"]["bootstrap"]
        self.assertEqual((bootstrap["released"], bootstrap["relockable"], bootstrap["queued_policy_jobs"]), (True, False, 0))
        self.assertEqual(env.actions.set_bootstrap(env.item("7", "series").id, {"released": False, "cancel_queued_policy": True})["cancelled"], 0)

    def test_cancelling_pulls_back_the_policys_queued_jobs_but_not_the_bootstraps_own_nor_the_admins(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        manual = env.actions.request({"items": env.ids("7104")})["created"][0]  # the admin's own request, asked for early
        env.actions.set_bootstrap(env.item("7", "series").id, {})
        answer = env.actions.set_bootstrap(env.item("7", "series").id, {"released": False, "cancel_queued_policy": True})
        self.assertEqual(answer, {"ok": True, "released": False, "cancelled": 2})  # 7103 and 7105: 7104 is the admin's
        self.assertEqual(sorted(j["media_id"] for j in env.jobs_open()), ["7101", "7102", "7104"])
        self.assertEqual(env.queue.jobs.get(manual)["origin"], "manual")
        ended = next(j for j in env.queue.jobs.by_state("cancelled") if j["media_id"] == "7103")
        self.assertEqual((ended["state"], ended["result"]), ("cancelled", "bootstrap_hold"))  # the row stays in Activity

    def test_holding_without_cancelling_keeps_every_job(self):
        env = self.env()
        self.release(env)
        answer = env.actions.set_bootstrap(env.item("7", "series").id, {"released": False})
        self.assertEqual(answer, {"ok": True, "released": False, "cancelled": 0})
        self.assertEqual(len(env.jobs_open()), 5)

    def test_letting_it_go_again_asks_for_exactly_the_episodes_that_were_pulled_back(self):
        env = self.env(auto_request_daily_cap=5)
        self.release(env)
        catalog_id = env.item("7", "series").id
        self.assertEqual(env.actions.set_bootstrap(catalog_id, {"released": False, "cancel_queued_policy": True})["cancelled"], 3)
        self.assertEqual(env.store.policy_jobs_since(0), 5 - 3)  # pulled back jobs do not count against the day's cap
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 2)  # held again: the policy asks for nothing more
        self.assertEqual(env.actions.set_bootstrap(catalog_id, {})["started"], True)
        self.assertEqual(self.requested(env), [1, 2, 3, 4, 5])  # rule D4 does not stand in the way of what was pulled back
        self.assertEqual(env.store.policy_jobs_since(0), 5)  # and the cap saw five, not eight

    def test_a_job_the_admin_cancelled_is_still_not_asked_for_again(self):
        env = self.env()
        self.release(env)
        victim = next(j for j in env.jobs_open() if j["media_id"] == "7105")
        env.queue.jobs.request_stop(victim["job_id"])  # the admin's own cancel: no ``bootstrap_hold``
        env.actions.set_bootstrap(env.item("7", "series").id, {"released": False, "cancel_queued_policy": True})
        env.actions.set_bootstrap(env.item("7", "series").id, {})
        self.assertEqual(self.requested(env), [1, 2, 3, 4])


class PureTests(unittest.TestCase):
    def test_the_rule_in_one_table(self):
        none, glossary = SeriesSeed(), SeriesSeed(glossary=True)
        rows = [
            (dict(limit=2, released=False, seed=none), True),  # nothing was ever looked at
            (dict(limit=2, released=True, seed=none), False),  # Continue
            (dict(limit=0, released=False, seed=none), False),  # the wait is off
            (dict(limit=2, released=False, seed=glossary), False),  # a glossary was approved
            (dict(limit=2, released=False, seed=SeriesSeed(glossary=True, pending=3)), False),  # more waits, but it was approved
            (dict(limit=2, released=False, seed=SeriesSeed(cards=1)), False),
            (dict(limit=2, released=False, seed=SeriesSeed(decided=2)), False),  # all decided, none approved
            (dict(limit=2, released=False, seed=SeriesSeed(decided=1, pending=1)), True),  # one rejected, one waits
            (dict(limit=2, released=False, seed=SeriesSeed(pending=4)), True),
        ]
        for kwargs, locked in rows:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(is_locked(**kwargs), locked)

    def test_state_has_a_quiet_default(self):
        self.assertEqual(BootstrapState(), BootstrapState(locked=False, limit=0, allowed=frozenset(), pending=0, released=False))


if __name__ == "__main__":
    unittest.main()
