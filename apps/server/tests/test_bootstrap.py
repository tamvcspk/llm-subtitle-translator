"""P13: the bootstrap of ``auto_request`` (SPEC FR-15.4, FR-15.12; ADR-10). A series with nothing approved yet gets only
its first episodes from the policy until its glossary has been reviewed. Fake Bazarr, fake model; nothing touches the
network."""

import unittest

from subserver.auto_request import AutoRequest, sync_and_request
from subserver.bootstrap import BootstrapState, is_locked, story_order
from subserver.catalog_api import handle
from subserver.ports import SeriesSeed
from subserver.suggestions import Proposal, SuggestionService

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

    def test_continue_lets_it_go_on_the_next_sync_and_holds_it_again_on_request(self):
        env = self.env()
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 2)
        catalog_id = env.item("7", "series").id
        self.assertEqual(env.actions.set_bootstrap(catalog_id, {}), {"ok": True, "released": True})
        self.assertEqual(len(env.jobs_open()), 2)  # nothing happens at once: the next sync asks
        self.assertEqual(self.page(env)["series"]["bootstrap"]["released"], True)
        sync_and_request(env.sync, env.auto)
        self.assertEqual(len(env.jobs_open()), 5)
        env.actions.set_bootstrap(catalog_id, {"released": False})
        self.assertTrue(is_locked(limit=2, released=False, seed=SeriesSeed()))

    def test_the_route_validates_and_finds_the_series(self):
        env = self.env()
        catalog_id = env.item("7", "series").id
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{catalog_id}/bootstrap", {})[1], {"ok": True, "released": True})
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{catalog_id}/bootstrap", {"released": "yes"})[0], 400)
        self.assertEqual(handle(env.actions, "PUT", "/api/catalog/series/999/bootstrap", {})[0], 404)
        episode = env.item("7101").id
        self.assertEqual(handle(env.actions, "PUT", f"/api/catalog/series/{episode}/bootstrap", {})[0], 404)


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
