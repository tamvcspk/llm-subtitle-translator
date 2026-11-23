"""P16: research grounding (SPEC FR-7, FR-4.4, FR-7.5, FR-7.6, criterion 23 a-g). Every provider is a fake; nothing touches the
network. The facts are the canned ones of the story-memory tests: "Kyber crystal" shows in every episode and "Pair Term" in the
first two, so two terms are worth looking up; "Lone Term" shows once and is not."""

import json
import re
import tempfile
import unittest

from subcore.providers import Capabilities, GroundingSource, LLMResponse, QuotaExhausted, ResilientProvider
from subcore.research import RESEARCH_PROMPT_VERSION

from subserver.adapters.settings import SettingsService
from subserver.adapters.store import Store
from subserver.api import suggestion_api
from subserver.domain import queue_policy as policy
from subserver.domain.config import DEFAULT_APP_CONFIG, model_for_task, normalize_app_config
from subserver.domain.ports import SUGGESTION_ACCEPTED, SUGGESTION_PENDING
from subserver.jobs.jobs import DONE, PUBLISH, READING_KINDS, RESEARCH, SKIPPED, WAITING, job_name
from subserver.jobs.read_jobs import ResearchJob
from subserver.services.research import AUTO_PRIO, Researcher, _Budget, ResearchBudgetReached
from subserver.services.suggestions import independent_sources

from .test_proposals import Env as ProposalEnv

REDIRECT = "https://redirect.test/r/"


class Resolver:
    """Follows a redirect the way the real one would: the page's own address, on the site the link names."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def resolve(self, url):
        self.calls.append(url)
        if self.fail:
            return None
        domain, number = url.rsplit("/", 1)[1].rsplit("-", 1)
        return f"https://www.{domain}/page-{number}"


class Recorder:
    """A telemetry that keeps what it is told."""

    def __init__(self):
        self.events = []
        self.calls = []

    def event(self, kind, **data):
        self.events.append((kind, data))

    def signal(self, *a, **k):
        return None

    def llm_call(self, job_id, log, **kw):
        self.calls.append((log, kw))

    def kinds(self):
        return [kind for kind, _ in self.events]


class Env(ProposalEnv):
    def __init__(self, test, *, sites=("wikipedia.org", "fandom.com"), refs=(0, 1), **settings):
        super().__init__(test, **{"suggest_from_facts": False, "research_enabled": True, **settings})
        self.fake.caps = Capabilities(web_search=True)
        self.sites, self.refs = sites, list(refs)
        self.search_error = None
        self.requests = []  # the research calls the model was asked
        self.models_used = []
        self.telemetry = Recorder()
        self.resolver = Resolver()
        self.researcher = Researcher(self.proposer, settings_source=lambda: self.settings, telemetry=self.telemetry, resolver=self.resolver)
        self.proposer.researcher = self.researcher
        self.suggestions.researcher = self.researcher

        def factory(settings, model, *, deadline=None, on_call=None):
            self.models_used.append(model)
            return ResilientProvider(self.fake, deadline=deadline, on_call=on_call, base_delay=0.0, max_attempts=1)

        self.handlers["research"] = ResearchJob(self.researcher, factory, settings_source=lambda: self.settings, usage=self.store)

    # -- the fake model ----------------------------------------------------------------------------
    def handler(self, req, call_no):
        if req.task != "research":
            return super().handler(req, call_no)
        self.requests.append(req)
        if req.web_search:
            if self.search_error is not None:
                raise self.search_error
            sources = tuple(GroundingSource(f"{REDIRECT}{site}-{n}", site, site) for n, site in enumerate(self.sites))
            return LLMResponse(
                "the web says so", tokens_in=50, tokens_out=20, grounding_sources=sources,
                search_queries=("how is it written",), cited_text="the web says so [0]",
            )
        listed = req.user.split("ITEMS:\n")[1].split("\n\nSOURCES")[0].splitlines()
        terms = []
        for line in listed:
            n, name = re.match(r"^(\d+)\. (.+?)(?: \(\w+\))?$", line).groups()
            terms.append({"i": int(n), "target": "VI " + name, "source_refs": self.refs, "note": "a place",
                          "url": "https://invented.example/page"})
        return {"terms": terms}

    def research(self, **extra):
        """The admin asks and the queue runs the job: the job's own record."""
        job_id, _ = self.researcher.request(self.series_id, "vi", **extra)
        self.run_jobs()
        return self.service.jobs.jobs.get(job_id)

    def proposals(self):
        return [i for i in self.suggestions.listing()["items"] if i["source"] == "research"]

    def queued(self, kind=RESEARCH):
        return [j for j in self.service.jobs.jobs.by_state("queued", "waiting", "running") if j["kind"] == kind]


def ready(test, **kw):
    """The facts of the six episodes are stored and no research job is waiting: the hook is off while they are read."""
    enabled = kw.pop("research_enabled", True)
    env = Env(test, research_enabled=False, **kw)
    env.translate(6)
    env.settings["research_enabled"] = enabled
    return env


class SettingsTests(unittest.TestCase):
    def test_research_is_off_by_default_with_a_budget_of_ten_calls_a_series(self):
        self.assertIs(DEFAULT_APP_CONFIG["research_enabled"], False)
        self.assertEqual(DEFAULT_APP_CONFIG["research_max_calls_per_series"], 10)
        self.assertIs(DEFAULT_APP_CONFIG["auto_accept_terms"], True)  # the one switch that applies a term (no research twin)
        self.assertNotIn("research_auto_accept", DEFAULT_APP_CONFIG)

    def test_the_switch_and_the_budget_are_normalised_like_the_other_settings(self):
        self.assertIs(normalize_app_config({"research_enabled": "on"})["research_enabled"], True)
        self.assertIs(normalize_app_config({"research_enabled": "off"})["research_enabled"], False)
        self.assertEqual(normalize_app_config({"research_max_calls_per_series": "4"})["research_max_calls_per_series"], 4)
        self.assertEqual(normalize_app_config({"research_max_calls_per_series": "x"})["research_max_calls_per_series"], 10)
        self.assertEqual(normalize_app_config({"research_max_calls_per_series": -3})["research_max_calls_per_series"], 0)  # no cap
        self.assertEqual(model_for_task({"gst_model": "light", "research_model": "searcher"}, "research"), "searcher")
        self.assertEqual(model_for_task({"gst_model": "light"}, "research"), "light")  # one model setting is enough

    def test_what_the_console_saves_reaches_the_running_worker(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        service = SettingsService(
            Store(":memory:"), config_path=f"{folder.name}/config.json",
            postprocess_targets_path=f"{folder.name}/postprocess/targets.json",
        )
        self.addCleanup(service.repo.close)
        service.start()
        self.assertEqual((service.settings()["research_enabled"], service.settings()["research_max_calls_per_series"]), (False, 10))
        service.save({"research_enabled": True, "research_max_calls_per_series": 0})
        runtime = service.settings()
        self.assertEqual((runtime["research_enabled"], runtime["research_max_calls_per_series"]), (True, 0))


class JobBasicsTests(unittest.TestCase):
    def test_a_research_job_reads_only_and_is_named_for_the_series(self):
        self.assertIn(RESEARCH, READING_KINDS)
        self.assertEqual(job_name({"kind": RESEARCH, "title": "Show"}), "Research terms · Show")

    def test_the_terms_to_look_up_are_those_two_episodes_share_and_nothing_else(self):
        env = ready(self)
        self.assertEqual({i.source for i in env.researcher.candidates(env.series_id, "vi")}, {"Kyber crystal", "Pair Term"})


class Criterion23aOncePerSeriesTests(unittest.TestCase):
    def test_a_run_makes_one_search_and_one_reading_and_a_second_run_asks_nothing(self):
        env = ready(self)
        first = env.research()
        self.assertEqual((first["state"], first["result"]), (DONE, "proposed"))
        self.assertEqual([r.web_search for r in env.requests], [True, False])
        self.assertEqual({r.task for r in env.requests}, {"research"})
        # what was proposed is in the inbox and what was asked is not asked again: no call is spent
        again = env.research()
        self.assertEqual(again["result"], "nothing-new")
        self.assertEqual(len(env.requests), 2)
        self.assertEqual(env.researcher.blocked(env.series_id, "vi"), "no_terms")

    def test_the_search_runs_on_the_research_model_and_the_reading_on_the_memory_model(self):
        env = ready(self, research_model="m-search", memory_model="m-cheap")
        del env.models_used[:]
        env.research()
        self.assertEqual(env.models_used, ["m-search", "m-cheap"])

    def test_a_term_the_web_gave_no_form_for_is_not_asked_again_at_once_nor_a_term_kept_as_it_is(self):
        env = ready(self)
        env.refs = []  # the reading finds no page behind any form
        first = env.research()
        self.assertEqual((first["result"], env.proposals()), ("nothing-new", []))
        env.research()
        self.assertEqual(len(env.requests), 2)  # remembered as unanswered: no new search


class Criterion23bSourcesFromGroundingTests(unittest.TestCase):
    def test_the_links_of_a_proposal_are_the_pages_the_provider_reported_not_what_the_model_wrote(self):
        env = ready(self)
        env.research()
        for item in env.proposals():
            self.assertEqual(item["sources"], ["https://www.wikipedia.org/page-0", "https://www.fandom.com/page-1"])
            self.assertNotIn("invented", json.dumps(item))
        self.assertEqual(len(env.resolver.calls), 2)  # each page is followed once, however many terms it backs

    def test_a_link_that_cannot_be_followed_stands_for_its_site(self):
        env = ready(self)
        env.resolver.fail = True
        env.research()
        self.assertEqual(env.proposals()[0]["sources"], ["https://wikipedia.org/", "https://fandom.com/"])
        self.assertEqual(independent_sources(env.proposals()[0]["sources"]), 2)

    def test_a_provider_with_no_name_for_the_site_and_no_link_leaves_the_term_unproposed(self):
        env = ready(self, sites=("",))  # one page, no domain; the redirect cannot be followed
        env.resolver.fail = True
        env.refs = [0]
        env.research()
        self.assertEqual(env.proposals(), [])


class Criterion23cResultsInSuggestionsTests(unittest.TestCase):
    def test_a_term_waits_in_the_inbox_as_research_with_its_cues_and_pages(self):
        env = ready(self, sites=("wikipedia.org",), refs=(0,))
        env.research()
        item = next(i for i in env.proposals() if i["payload"]["source"] == "Pair Term")
        self.assertEqual((item["kind"], item["status"], item["lang"]), ("term", SUGGESTION_PENDING, "vi"))
        self.assertEqual(item["payload"], {"source": "Pair Term", "target": "VI Pair Term", "note": "a place"})
        self.assertEqual({e["episode"] for e in item["evidence"]}, {"S01E01", "S01E02"})
        self.assertEqual(item["evidence"][0]["unit_id"], env.unit_id(1))  # the card links to the episode
        self.assertEqual(len(item["sources"]), 1)
        self.assertTrue(env.glossary().is_empty())  # nothing is applied by itself

    def test_a_proposal_the_admin_turned_down_is_never_asked_again(self):
        env = ready(self, sites=("wikipedia.org",), refs=(0,))
        env.research()
        for item in env.proposals():
            env.suggestions.reject(item["id"])
        self.assertEqual(env.researcher.candidates(env.series_id, "vi"), [])


class Criterion23dAutoAcceptTests(unittest.TestCase):
    def test_two_independent_sites_apply_the_term_by_themselves_and_it_can_be_undone(self):
        env = ready(self)  # auto_accept_terms is on by default
        job = env.research()
        self.assertEqual(job["report"]["auto_accepted"], 2)
        self.assertEqual({i["status"] for i in env.proposals()}, {SUGGESTION_ACCEPTED})
        self.assertEqual({i["auto_accepted"] for i in env.proposals()}, {True})
        self.assertEqual({t.target for t in env.glossary().terms}, {"VI Kyber crystal", "VI Pair Term"})
        undone = env.suggestions.undo(env.proposals()[0]["id"])
        self.assertEqual(undone["suggestion"]["status"], SUGGESTION_PENDING)

    def test_one_site_or_two_pages_of_one_site_wait_for_the_admin(self):
        env = ready(self, sites=("wikipedia.org", "wikipedia.org"), refs=(0, 1))
        env.research()
        self.assertEqual({i["status"] for i in env.proposals()}, {SUGGESTION_PENDING})
        self.assertTrue(env.glossary().is_empty())

    def test_the_switch_of_the_setting_and_the_tick_of_the_box_both_decide_for_their_run(self):
        env = ready(self, auto_accept_terms=False)
        env.research()
        self.assertEqual({i["status"] for i in env.proposals()}, {SUGGESTION_PENDING})
        ticked = ready(self, auto_accept_terms=False)
        ticked.research(auto_accept=True)  # the box says yes for this run
        self.assertEqual({i["status"] for i in ticked.proposals()}, {SUGGESTION_ACCEPTED})
        unticked = ready(self)  # the setting is on
        unticked.research(auto_accept=False)
        self.assertEqual({i["status"] for i in unticked.proposals()}, {SUGGESTION_PENDING})


class Criterion23eNoWebSearchTests(unittest.TestCase):
    def test_a_provider_without_a_search_tool_is_skipped_without_a_fault_and_nothing_is_queued(self):
        env = Env(self, llm_provider="openai_compat")
        env.translate(6)
        self.assertEqual(env.queued(), [])  # the hook leaves no job (FR-7.3)
        job = env.research()  # one made by hand anyway
        self.assertEqual((job["state"], job["result"]), (SKIPPED, "skipped-no-web-search"))
        self.assertEqual(env.requests, [])
        self.assertEqual(env.researcher.estimate(env.series_id, "vi")["blocked"], "no_web_search")
        with self.assertRaises(ValueError) as refused:
            env.suggestions.research(env.series_id, "vi")
        self.assertIn("Gemini", str(refused.exception))


class Criterion23fAJobOfItsOwnTests(unittest.TestCase):
    def test_new_facts_leave_a_research_job_above_the_translations_still_waiting_and_without_auto_request(self):
        env = Env(self)
        env.settings["auto_request"] = False
        env.translate(6)
        (job,) = env.queued()
        self.assertEqual((job["origin"], job["prio"], job["mode"]), ("facts", AUTO_PRIO, RESEARCH))
        self.assertGreater(AUTO_PRIO, policy.INTERACTIVE)  # above the admin's own translations too
        self.assertEqual(job["target_code"], "vi")

    def test_it_runs_before_the_translations_that_are_still_waiting(self):
        env = Env(self)
        env.translate(6)
        order = []
        env.handlers[PUBLISH] = lambda job, update: (order.append("publish"), "translated")[1]
        orig = env.handlers["research"]
        env.handlers["research"] = lambda job, update: (order.append("research"), orig(job, update))[1]
        for n in (3, 4):  # the admin's own translations, queued before the research is
            env.service.jobs.jobs.add(
                {"subtitle_path": str(env.source(n)), "series_id": "7", "target_code": "vi"}, key=f"pub-{n}", kind=PUBLISH,
                origin="manual", prio=policy.INTERACTIVE,
            )
        env.run_jobs()
        self.assertEqual(order, ["research", "publish", "publish"])

    def test_there_is_one_open_job_per_series_and_a_second_request_is_that_job(self):
        env = Env(self)
        env.translate(6)
        env.translate(5)  # more facts changed nothing: the facts are stored already
        self.assertEqual(len(env.queued()), 1)
        again, new = env.researcher.request(env.series_id, "vi")
        self.assertFalse(new)
        self.assertEqual(env.queued()[0]["prio"], AUTO_PRIO)  # the admin's request lifts nothing it already outranks

    def test_the_switch_is_off_by_default_and_then_only_the_admin_can_ask(self):
        env = Env(self, research_enabled=False)
        env.translate(6)
        self.assertEqual(env.queued(), [])
        self.assertEqual(env.research()["result"], "proposed")  # the button always works

    def test_nothing_to_look_up_queues_nothing(self):
        env = Env(self)
        env.translate(1)  # one episode: no term is in two
        self.assertEqual(env.queued(), [])

    def test_the_hook_never_raises_into_the_translation_job(self):
        env = Env(self)
        env.proposer.jobs = type("Broken", (), {"enqueue_research": lambda *a, **k: 1 / 0})()
        env.translate(6)  # would raise if the hook did
        self.assertIn("research.queue_failed", env.telemetry.kinds())

    def test_a_search_that_runs_out_of_quota_makes_only_the_research_wait_and_leaves_the_model_alone(self):
        env = Env(self)
        env.translate(6)
        env.search_error = QuotaExhausted("grounding quota")
        ran = []
        env.handlers[PUBLISH] = lambda job, update: (ran.append(job["job_id"]), "translated")[1]
        env.service.jobs.jobs.add(
            {"subtitle_path": str(env.source(3)), "series_id": "7", "target_code": "vi"}, key="pub-3", kind=PUBLISH,
            origin="manual", prio=policy.RETRANSLATE,
        )
        env.run_jobs()
        research = env.service.jobs.jobs.by_state(WAITING)[0]
        self.assertEqual((research["kind"], research["wait_reason"]), (RESEARCH, "rate"))
        self.assertGreater(research["not_before"], 0)
        self.assertEqual(ran, ["pub-3"])  # the translation ran, on the model the research could not rest
        health = env.service.jobs.jobs.health_map()
        self.assertEqual({h.state for h in health.values()}, {policy.CLOSED})  # no quota, no breaker: the model is as it was

    def test_a_failed_search_costs_nothing_the_translation_needs(self):
        env = Env(self)
        env.translate(6)
        env.fake.handler = lambda req, n: (_ for _ in ()).throw(RuntimeError("boom")) if req.task == "research" else env.handler(req, n)
        job = env.research()
        self.assertEqual(job["state"], "failed")
        self.assertEqual(env.translate(5), "translated")  # the next translation is not touched by it


class Criterion23gBudgetTests(unittest.TestCase):
    def test_every_call_is_recorded_with_its_series_and_the_budget_reads_them_after_the_jobs_are_cleaned_away(self):
        env = ready(self, research_max_calls_per_series=4)
        env.research()
        calls = [c for c in env.store.llm_calls() if c.task == "research"]
        self.assertEqual(len(calls), 2)
        self.assertEqual({c.series_id for c in calls}, {env.series_id})
        self.assertEqual({c.prompt_version for c in calls}, {f"research-{RESEARCH_PROMPT_VERSION}"})
        with env.store.transaction() as db:
            db.execute("DELETE FROM jobs")  # the finished rows are cleaned up, the calls stay
        self.assertEqual(env.researcher.used(env.series_id), 2)

    def test_a_series_that_has_used_its_calls_is_not_queued_nor_runnable_and_says_why(self):
        env = ready(self, research_max_calls_per_series=3)
        env.research()  # uses 2 of 3: one is left, and a search with no call to read it would be wasted
        self.assertEqual(env.researcher.blocked(env.series_id, "vi"), "budget")
        estimate = env.researcher.estimate(env.series_id, "vi")
        self.assertEqual((estimate["blocked"], estimate["budget"]), ("budget", {"cap": 3, "used": 2, "after": 2}))
        self.assertIn("budget", estimate["reason"])
        with self.assertRaises(ValueError):
            env.suggestions.research(env.series_id, "vi")

    def test_a_run_that_finds_the_budget_spent_stops_before_any_call_with_an_event_and_ends_done(self):
        env = ready(self, research_max_calls_per_series=2)
        env.store.record_llm_call("old", provider="gemini", model="m", task="research", tokens_in=1, tokens_out=1, latency_ms=1,
                                  outcome="ok", series_id=env.series_id)
        job = env.research()  # asked by hand: the request itself is not refused, the run is
        self.assertEqual((job["state"], job["result"]), (DONE, "budget-reached"))
        self.assertEqual(env.requests, [])
        self.assertIn("research.budget_reached", env.telemetry.kinds())

    def test_the_cap_of_zero_is_no_cap(self):
        env = ready(self, research_max_calls_per_series=0)
        for n in range(30):
            env.store.record_llm_call(f"old{n}", provider="gemini", model="m", task="research", tokens_in=1, tokens_out=1,
                                      latency_ms=1, outcome="ok", series_id=env.series_id)
        self.assertEqual(env.researcher.blocked(env.series_id, "vi"), "")
        self.assertEqual(env.research()["result"], "proposed")

    def test_the_budget_stops_the_call_that_would_pass_it(self):
        budget = _Budget(cap=2, used=1)
        budget.spend()
        with self.assertRaises(ResearchBudgetReached):
            budget.spend()
        free = _Budget(cap=0, used=999)
        for _ in range(5):
            free.spend()
        self.assertEqual(free.made, 5)

    def test_calls_of_other_series_and_other_tasks_do_not_count(self):
        env = ready(self, research_max_calls_per_series=2)
        env.store.record_llm_call("x", provider="g", model="m", task="research", tokens_in=1, tokens_out=1, latency_ms=1,
                                  outcome="ok", series_id=env.series_id + 99)
        env.store.record_llm_call("y", provider="g", model="m", task="extract", tokens_in=1, tokens_out=1, latency_ms=1,
                                  outcome="ok", series_id=env.series_id)
        self.assertEqual(env.researcher.used(env.series_id), 0)


class EstimateTests(unittest.TestCase):
    def test_the_estimate_says_what_would_be_asked_on_which_models_and_changes_nothing(self):
        env = ready(self, research_model="m-search", memory_model="m-cheap", max_calls_per_day=10)
        before = (len(env.fake.calls), env.suggestions.listing()["counts"])
        estimate = env.suggestions.research(env.series_id, "vi", estimate_only=True)
        self.assertEqual(
            (estimate["terms"], estimate["asked"], estimate["calls"], estimate["blocked"]), (2, 2, 2, ""),
        )
        self.assertEqual((estimate["search_model"], estimate["read_model"]), ("m-search", "m-cheap"))
        self.assertEqual(estimate["budget"], {"cap": 10, "used": 0, "after": 2})
        self.assertEqual((estimate["auto_accept"], estimate["calls_left_today"]), (True, 10))
        self.assertEqual((len(env.fake.calls), env.suggestions.listing()["counts"]), before)
        self.assertEqual(env.queued(), [])

    def test_no_term_is_a_reason_not_an_error_and_the_route_answers_for_both(self):
        env = Env(self)
        env.translate(1)
        body = {"series_id": env.series_id, "lang": "vi", "estimate_only": True}
        status, answer = suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/research", {}, body)
        self.assertEqual((status, answer["blocked"], answer["terms"], answer["calls"]), (200, "no_terms", 0, 0))
        status, answer = suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/research", {}, {**body, "estimate_only": False})
        self.assertEqual(status, 400)
        self.assertEqual(suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/research", {}, {"lang": "vi"})[0], 400)
        self.assertEqual(suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/research", {}, {"series_id": 999, "lang": "vi"})[0], 404)

    def test_the_route_queues_one_job_and_a_second_request_is_merged_into_it(self):
        env = ready(self)
        body = {"series_id": env.series_id, "lang": "vi", "auto_accept": False}
        status, first = suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/research", {}, body)
        _, second = suggestion_api.handle(env.suggestions, "POST", "/api/suggestions/research", {}, body)
        self.assertEqual((status, first["queued"], second["merged"]), (200, 1, 1))
        (job,) = env.queued()
        self.assertEqual((job["origin"], job["prio"], job["auto_accept"]), ("manual", policy.INTERACTIVE, False))


if __name__ == "__main__":
    unittest.main()
