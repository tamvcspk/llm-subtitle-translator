"""P15e (ADR-18 part 3): every JSON answer of the console fits the model its route declares.

The models in ``subserver/api/answers`` are the contract that ``docs/openapi.json`` and the TypeScript types of ``apps/web``
come from, and the routes never validate them at run time. This is what keeps them true: the real worker is started on seeded
data (``tests/contract_kit.py``), each route is called and its answer is checked against the model of the route, which
forbids a field it does not name. A route with no model, and a route no case here calls, fail the last test, so a new route
cannot slip in undocumented.
"""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from subserver.api.answers.console import QueueCounts, QueueJob
from subserver.api.answers.insights import Insights
from subserver.api.answers.library import AdoptAnswer, CorrectionAdded, UnitContextAnswer
from subserver.api.answers.style import PresetImpact, StylePreview
from subserver.api.answers.suggestions import SearchSuggestions, Suggestion
from tests.contract_kit import APP, COVERED, ContractCase as Contract, check_answer

# Routes that answer something other than JSON, or that this seeded worker cannot reach with a 200. Each says why.
NOT_JSON = {
    ("GET", "/api/backups/download"): "a zip file",
    ("GET", "/api/series/{unit_id:int}/glossary.yaml"): "a YAML file",
    ("GET", "/api/movies/{unit_id:int}/glossary.yaml"): "a YAML file",
}
NOT_DRIVEN = {
    ("POST", "/api/auth/password"): "needs an account: with authentication off it is a 409 (test_console_login covers it)",
    ("GET", "/api/jobs/{job_id:path}/spec"): "needs a job that has translated: the answer is the bundle, any JSON object",
    ("POST", "/api/queue/request-prerequisites"): "needs a job that waits for earlier episodes (test_queue_requests covers the logic)",
    ("GET", "/api/suggestions/search/{p1:int}"): "needs a research search with Search Suggestions: checked on its model below, and in test_research",
    ("POST", "/api/series/{p1:int}/style/preview"): "calls the model: checked on the use case with a fake provider below",
    ("POST", "/api/movies/{p1:int}/style/preview"): "calls the model: checked on the use case with a fake provider below",
}

class ConsoleAnswers(Contract):
    """Account, status, queue, settings, keys, languages, models, logs, events, backups, clean-up and the connection test."""

    def test_console_routes(self):
        me = self.answer("GET", "/api/auth/me")
        self.assertEqual((me["user"], me["via"]), (None, "off"))
        self.answer("POST", "/api/auth/login", {"user": "x", "password": "y"})
        self.answer("POST", "/api/auth/logout", {})

        status = self.answer("GET", "/api/status")
        self.assertEqual(status["bazarr_url"], "http://127.0.0.1:1")
        settings = self.answer("GET", "/api/settings")
        self.assertEqual(settings["bazarr_url"], "http://127.0.0.1:1")
        saved = self.answer("POST", "/api/settings", {"log_level": "INFO"})
        self.assertEqual(saved["log_level"], "INFO")
        self.answer("GET", "/api/languages")
        self.answer("GET", "/api/gemini-models")
        self.answer("GET", "/api/logs")
        self.answer("POST", "/api/logs/clear", {})
        self.answer("GET", "/api/events")
        self.answer("GET", "/api/events?kind=job.&limit=5")

        # A job in the queue, so the queue answer has a row to describe.
        catalog = self.answer("GET", "/api/catalog")
        series = next(row for row in catalog["series"] if row["missing"])
        asked = self.answer("POST", "/api/jobs", {"series": series["id"], "languages": ["vi"]})
        self.assertEqual(asked["count"], 1)
        queue = self.answer("GET", "/api/queue")
        job = queue["pending"][0] if queue["pending"] else queue["deferred"][0]
        self.assertTrue(job["job_id"] and job["title"])

        estimate = self.answer("POST", "/api/queue/retry", {"all": True, "estimate_only": True})
        self.assertTrue(estimate["estimate_only"])
        self.answer("POST", "/api/queue/retry", {"all": True})
        self.answer("POST", "/api/queue/retry", {"job_id": "no-such-job"})
        self.answer("POST", "/api/queue/stop", {"job_id": job["job_id"]})
        self.answer("POST", "/api/queue/delete", {"state": "cancelled", "job_id": job["job_id"]})
        self.answer("POST", "/api/queue/cancel", {"job_id": "no-such-job"})

        key = self.answer("POST", "/api/keys", {"provider": "openai_compat", "value": "sk-contract-test-0001", "label": "contract"})
        self.assertEqual(key["last4"], "0001")
        self.assertEqual(len(self.answer("GET", "/api/keys")["items"]), 1)
        self.answer("PUT", f"/api/keys/{key['id']}", {"label": "renamed"})
        self.answer("POST", f"/api/keys/{key['id']}", {"label": "renamed again"})
        tested = self.answer("POST", f"/api/keys/{key['id']}/test", {})
        self.assertFalse(tested["ok"])  # no base URL: nothing to ask, so no network call either
        self.answer("POST", f"/api/keys/{key['id']}/remove", {})
        self.answer("POST", "/api/test-connection", {"kind": "bazarr", "bazarr_url": "http://127.0.0.1:1"})

        self.answer("POST", "/api/cleanup", {})
        created = self.answer("POST", "/api/backups", {})
        self.answer("GET", "/api/backups")
        code, archive = self.worker.download(f"/api/backups/download?name={created['backup']['name']}")
        self.assertEqual(code, 200)
        self.answer_raw("POST", "/api/backups/import", archive)


class CatalogAnswers(Contract):
    """The Library: what Bazarr lists, the labels, a request for translations, Ignored and the per-series policy."""

    def test_catalog_routes(self):
        overview = self.answer("GET", "/api/catalog")
        self.assertEqual(overview["languages"], ["vi"])
        status = self.answer("GET", "/api/catalog/status")
        self.assertIsNone(status["shrink"])
        series = next(row for row in overview["series"] if row["title"] == "Arcane Demo")
        page = self.answer("GET", f"/api/catalog/series/{series['id']}")
        missing = [item for item in page["items"] if any(t["content"] == "missing" for t in item["targets"])]
        self.assertEqual(len(missing), 1)
        movies = self.answer("GET", "/api/catalog/movies")
        self.assertEqual(len(movies["items"]), 1)

        self.assertTrue(self.answer("POST", "/api/catalog/sync", {})["requested"])
        self.assertEqual(self.answer("PUT", f"/api/catalog/series/{series['id']}/policy", {"auto_request": "on"})["auto_request"], "on")
        held = self.answer("GET", f"/api/catalog/series/{series['id']}")
        self.assertEqual(held["series"]["auto_request"], "on")
        self.answer("PUT", f"/api/catalog/series/{series['id']}/policy", {"auto_request": ""})
        # The three shapes of the bootstrap route, with the policy off for the series so letting it go asks for nothing.
        bootstrap = f"/api/catalog/series/{series['id']}/bootstrap"
        self.assertTrue(self.answer("PUT", bootstrap, {"estimate_only": True})["estimate_only"])
        self.assertIn("started", self.answer("PUT", bootstrap, {"released": True}))
        self.assertIn("cancelled", self.answer("PUT", bootstrap, {"released": False, "cancel_queued_policy": True}))
        self.assertEqual(
            sorted(self.answer("GET", f"/api/catalog/series/{series['id']}")["series"]["bootstrap"]),
            sorted(["locked", "limit", "pending", "released", "held", "held_by_lang", "no_source", "glossary_empty", "open_jobs",
                    "memory_on", "queued_policy_jobs", "relockable"]),
        )

        movie = movies["items"][0]["id"]
        self.assertEqual(self.answer("POST", "/api/catalog/ignore", {"items": [movie], "languages": ["vi"]})["changed"], 1)
        self.assertEqual(self.answer("GET", "/api/catalog/movies")["items"][0]["targets"][0]["ignored"], True)
        self.answer("POST", "/api/catalog/unignore", {"items": [movie], "languages": ["vi"]})

        estimate = self.answer("POST", "/api/jobs", {"series": series["id"], "languages": ["vi"], "estimate_only": True})
        self.assertEqual(estimate["planned_total"], 1)
        made = self.answer("POST", "/api/jobs", {"series": series["id"], "languages": ["vi"]})
        self.assertEqual(made["count"], 1)
        self.answer("POST", "/api/jobs", {"series": series["id"], "languages": ["vi"], "mode": "replace"})
        waiting = self.answer("GET", f"/api/catalog/series/{series['id']}")
        labels = [t for item in waiting["items"] for t in item["targets"] if t["job"]]
        self.assertTrue(labels and labels[0]["job_info"]["job_id"])


class LibraryAnswers(Contract):
    """Units, cues, corrections, versions, the comparison of a candidate, story memory and glossaries."""

    def test_library_routes(self):
        listed = self.answer("GET", "/api/library/series")
        series = next(row for row in listed["items"] if row["title"] == "Arcane Demo")
        sid = series["id"]
        units = self.answer("GET", f"/api/library/series/{sid}/units")
        self.answer("GET", f"/api/library/series/{sid}/units?status=current")
        movie = self.movie_unit()
        self.answer("GET", "/api/library/movies?status=untranslated")
        episode = next(u["id"] for u in units["units"] if u["episode"] == 1)
        other = next(u["id"] for u in units["units"] if u["episode"] == 2)
        detail = self.answer("GET", f"/api/units/{episode}")
        self.assertEqual(detail["targets"][0]["lang"], "vi")
        self.assertTrue(detail["memory_applicable"])

        cues = self.answer("GET", f"/api/units/{episode}/cues?lang=vi")
        self.assertEqual(cues["total"], 12)
        self.assertEqual(self.answer("GET", f"/api/units/{episode}/cues?lang=vi&at=00:00:05")["focus"], 3)

        # Corrections: an episode term, an episode note, a cue edit, and a wider scope that writes the glossary.
        added = self.answer("POST", f"/api/units/{episode}/corrections", {
            "lang": "vi", "kind": "term", "payload": {"source": "Kyber", "target": "Kyber"},
        })
        self.answer("POST", f"/api/units/{episode}/corrections", {"lang": "vi", "kind": "note", "payload": {"text": "Zed jokes"}})
        edited = self.answer("POST", f"/api/units/{episode}/corrections", {
            "lang": "vi", "kind": "cue_edit", "payload": {"cue": 1, "text": "Bạn có mang tinh thể không?"},
        })
        self.assertTrue(edited["version"])
        forward = self.answer("POST", f"/api/units/{episode}/corrections", {
            "lang": "vi", "kind": "address", "scope": "forward",
            "payload": {"a": "Zed", "b": "Kyber", "a_to_b": "anh–em"},
        })
        self.assertIn("impact", forward)
        self.assertIsNotNone(forward["from"])
        self.assertEqual(len(self.answer("GET", f"/api/units/{episode}/corrections?lang=vi")["items"]), 3)
        self.answer("POST", f"/api/units/{episode}/corrections/{added['ids'][0]}/remove", {})

        # Versions, the candidate and its comparison.
        versions = self.answer("GET", f"/api/units/{episode}/versions?lang=vi")
        candidate = next(v for v in versions["versions"] if v["status"] == "candidate")
        self.assertEqual(candidate["params"]["variant"]["label"], "warmer")
        current = next(v for v in versions["versions"] if v["current"])
        compare = self.answer("GET", f"/api/units/{episode}/versions/{candidate['version']}/compare?lang=vi")
        self.assertGreater(compare["differing"], 0)
        self.answer("GET", f"/api/units/{episode}/versions/{candidate['version']}/diff?lang=vi&against={current['version']}")
        self.answer("GET", f"/api/units/{episode}/versions/{current['version']}/diff?lang=vi")

        self.answer("POST", f"/api/units/{episode}/versions/{candidate['version']}/judge", {"lang": "vi"}, status=400)  # off
        self.answer("POST", "/api/settings", {"judge_enabled": True})
        estimate = self.answer("POST", f"/api/units/{episode}/versions/{candidate['version']}/judge", {"lang": "vi", "estimate_only": True})
        self.assertTrue(estimate["estimate_only"])
        queued = self.answer("POST", f"/api/units/{episode}/versions/{candidate['version']}/judge", {"lang": "vi"})
        self.assertTrue(queued["new"])
        with_judge = self.answer("GET", f"/api/units/{episode}/versions/{candidate['version']}/compare?lang=vi")
        self.assertEqual(with_judge["judge"]["job"]["job_id"], queued["job_id"])
        chosen = self.answer("POST", f"/api/units/{episode}/versions/{candidate['version']}/choose", {
            "lang": "vi", "picks": {"2": "variant", "3": "current"}, "whole": "tie",
        })
        self.assertEqual(chosen["recorded"], 3)
        applied = self.answer("POST", f"/api/units/{episode}/versions/{candidate['version']}/choose", {
            "lang": "vi", "picks": {"4": "variant"}, "apply": True,
        })
        self.assertEqual(applied["applied"]["edited_cues"], [4])

        retranslate = self.answer("POST", f"/api/units/{episode}/retranslate", {"lang": "vi", "range": {"cues": [2, 4]}, "estimate_only": True})
        self.assertTrue(retranslate["estimate_only"])
        self.answer("POST", f"/api/units/{episode}/retranslate", {"lang": "vi", "range": {"cues": [2, 4]}})
        self.answer("POST", f"/api/units/{episode}/variant", {"lang": "vi", "label": "colder", "style": {"tone": "cold"}, "estimate_only": True})
        self.answer("POST", f"/api/units/{episode}/variant", {"lang": "vi", "label": "colder", "style": {"tone": "cold"}})

        dry = self.answer("POST", f"/api/units/{episode}/versions/{candidate['version']}/adopt", {
            "lang": "vi", "apply_style": True, "dry_run": True,
        })
        self.assertIsNone(dry["adopted"])
        adopted = self.answer("POST", f"/api/units/{episode}/versions/{candidate['version']}/adopt", {"lang": "vi", "apply_style": True})
        self.assertEqual(adopted["adopted"], candidate["version"])
        reverted = self.answer("POST", f"/api/units/{episode}/versions/{current['version']}/revert", {"lang": "vi"})
        self.assertEqual(reverted["restored"], current["version"])

        # Story memory.
        context = self.answer("GET", f"/api/units/{episode}/context?lang=vi")
        self.assertTrue(context["applicable"])
        self.assertFalse(self.answer("GET", f"/api/units/{movie}/context")["applicable"])
        self.assertFalse(self.answer("GET", f"/api/units/{episode}/facts")["stored"])
        self.answer("POST", f"/api/units/{episode}/context/extract", {})
        self.assertEqual(len(self.answer("GET", f"/api/series/{sid}/memory")["units"]), 2)  # the two with a unit
        self.answer("PUT", f"/api/series/{sid}/memory", {"policy": "on", "specials_after": "S01E02"})
        self.assertEqual(self.answer("POST", f"/api/series/{sid}/memory/adopt", {})["ok"], True)
        self.assertEqual(len(self.answer("GET", f"/api/series/{sid}/memory")["units"]), 3)  # adopting made the third

        # Glossaries of a series and of a movie.
        glossary = self.answer("GET", f"/api/series/{sid}/glossary?lang=vi")
        self.assertTrue(glossary["exists"])
        doc = {**glossary["doc"], "terms": [*glossary["doc"].get("terms", []), {"source": "Hex", "target": "Hex", "locked": True, "from": "S01E02"}]}
        self.answer("PUT", f"/api/series/{sid}/glossary?lang=vi", {"doc": doc, "dry_run": True})
        saved = self.answer("PUT", f"/api/series/{sid}/glossary?lang=vi", {"doc": doc})
        history = self.answer("GET", f"/api/series/{sid}/glossary/history?lang=vi")
        self.assertTrue(history["items"])
        self.answer("POST", f"/api/series/{sid}/glossary/restore?lang=vi", {"version": history["items"][0]["version"], "dry_run": True})
        self.answer("POST", f"/api/series/{sid}/glossary/restore?lang=vi", {"version": history["items"][0]["version"]})
        others = [row["id"] for row in listed["items"] if row["id"] != sid]
        self.assertEqual(self.answer("POST", f"/api/series/{sid}/glossary/copy?lang=vi", {"from_series": others[0], "merge": True})["from"], "Second Demo")
        self.assertGreater(saved["version"], 0)
        code, text = self.worker.download(f"/api/series/{sid}/glossary.yaml?lang=vi")
        self.assertEqual(code, 200)
        self.assertIn(b"terms", text)
        self.answer("GET", f"/api/movies/{movie}/glossary?lang=vi")
        for target in ("b", "c"):  # the history holds what a save replaced: the second save has something to keep
            self.answer("PUT", f"/api/movies/{movie}/glossary?lang=vi", {"doc": {"version": 1, "terms": [{"source": "a", "target": target}]}})
        earlier = self.answer("GET", f"/api/movies/{movie}/glossary/history?lang=vi")["items"][0]["version"]
        self.answer("POST", f"/api/movies/{movie}/glossary/restore?lang=vi", {"version": earlier, "dry_run": True})

        self.answer("POST", f"/api/units/{other}/output/remove", {"lang": "vi"})


class StyleAnswers(Contract):
    """The Style page: the style of a series, a movie and an episode, presets and character cards."""

    def test_style_routes(self):
        listed = self.answer("GET", "/api/library/series")
        sid = next(row["id"] for row in listed["items"] if row["title"] == "Arcane Demo")
        movie = self.movie_unit()
        episode = next(u["id"] for u in self.answer("GET", f"/api/library/series/{sid}/units")["units"] if u["episode"] == 1)

        overview = self.answer("GET", f"/api/series/{sid}/style?lang=vi")
        self.assertEqual(overview["series"]["title"], "Arcane Demo")
        self.assertNotIn("movie", overview)
        self.assertEqual(overview["preset"], "drama")
        self.assertEqual(self.answer("GET", f"/api/movies/{movie}/style?lang=vi")["movie"]["id"], movie)
        self.answer("PUT", f"/api/series/{sid}/style?lang=vi", {"preset": "drama", "style": {"tone": "grave", "max_cps": 17}, "dry_run": True})
        self.answer("PUT", f"/api/series/{sid}/style?lang=vi", {"preset": "drama", "style": {"tone": "grave", "max_cps": 17}})
        self.answer("PUT", f"/api/movies/{movie}/style?lang=vi", {"style": {"formality": "casual"}})
        self.answer("GET", f"/api/series/{sid}/style/suggestions")

        card = {"name": "Kyber", "aliases": ["K"], "speech": "dry", "address": [{"to": "Zed", "form": "anh–em"}],
                "catchphrases": ["Not here"], "style": {"formality": "casual"}, "always": True}
        self.answer("PUT", f"/api/series/{sid}/cards?lang=vi", {"card": card, "dry_run": True})
        saved = self.answer("PUT", f"/api/series/{sid}/cards?lang=vi", {"card": card})
        self.assertEqual(saved["card"]["name"], "Kyber")
        self.assertEqual(len(self.answer("GET", f"/api/series/{sid}/style?lang=vi")["cards"]), 2)
        self.answer("POST", f"/api/series/{sid}/cards/delete?lang=vi", {"name": "Kyber"})
        self.answer("PUT", f"/api/movies/{movie}/cards?lang=vi", {"card": {"name": "Mo"}})
        self.answer("POST", f"/api/movies/{movie}/cards/delete?lang=vi", {"name": "Mo"})
        self.answer("PUT", f"/api/series/{sid}/characters?lang=vi", {"glossary": {"version": 1, "characters": [{"name": "Zed", "target": "Zed"}]}})
        self.answer("PUT", f"/api/movies/{movie}/characters?lang=vi", {"glossary": {"version": 1, "characters": [{"name": "Mo"}]}})

        presets = self.answer("GET", "/api/style/presets")
        self.assertGreaterEqual(len(presets["items"]), 5)
        self.answer("PUT", "/api/style/presets/drama", {"name": "Drama", "genres": ["Drama"], "style": {"tone": "grave"}, "dry_run": True})
        self.answer("PUT", "/api/style/presets/drama", {"name": "Drama", "genres": ["Drama"], "style": {"tone": "grave"}})
        cloned = self.answer("POST", "/api/style/presets/drama/clone", {"name": "My drama"})
        self.answer("POST", f"/api/style/presets/{cloned['id']}/delete", {"dry_run": True})
        self.answer("POST", f"/api/style/presets/{cloned['id']}/delete", {})

        self.assertEqual(self.answer("GET", f"/api/units/{episode}/style")["style"], {})
        self.answer("PUT", f"/api/units/{episode}/style", {"style": {"tone": "tense"}, "dry_run": True})
        self.answer("PUT", f"/api/units/{episode}/style", {"style": {"tone": "tense"}})


class ReviewAnswers(Contract):
    """The Review inbox, and the proposals the admin's corrections make."""

    edit_demo = True  # a small episode whose machine text says "Mẹ" for "Mom": three corrections are a pattern to find

    def test_review_routes(self):
        listed = self.answer("GET", "/api/library/series")
        sid = next(row["id"] for row in listed["items"] if row["title"] == "Edit Demo")
        unit = self.answer("GET", f"/api/library/series/{sid}/units")["units"][0]["id"]
        self.assertEqual(self.answer("GET", "/api/suggestions")["pending"], 0)
        self.answer("GET", "/api/suggestions/summary")
        estimate = self.answer("POST", "/api/suggestions/generate", {"series_id": sid, "lang": "vi", "estimate_only": True})
        self.assertTrue(estimate["estimate_only"])
        queued = self.answer("POST", "/api/suggestions/generate", {"series_id": sid, "lang": "vi"})
        self.assertEqual(queued["queued"], 1)
        again = self.answer("POST", "/api/suggestions/generate", {"series_id": sid, "lang": "vi"})
        self.assertEqual(again["merged"], 1)
        # Research (P16): nothing to look up in a series with no facts, so the estimate says why a run is not made.
        research = self.answer("POST", "/api/suggestions/research", {"series_id": sid, "lang": "vi", "estimate_only": True})
        self.assertEqual((research["terms"], research["calls"], research["blocked"]), (0, 0, "no_terms"))

        # The admin corrects "Mẹ" in three cues: the feedback loop proposes the term, with the cues as evidence.
        rows = self.answer("GET", f"/api/units/{unit}/cues?lang=vi")["rows"]
        for row in [r for r in rows if "Mẹ" in (r["target"] or "")][:3]:
            self.answer("POST", f"/api/units/{unit}/corrections", {
                "lang": "vi", "kind": "cue_edit", "payload": {"cue": row["n"], "text": row["target"].replace("Mẹ", "Má")},
            })
        found = self.answer("POST", "/api/suggestions/from-edits", {"series_id": sid, "lang": "vi"})
        self.assertEqual((found["cues"], found["patterns"]), (3, 1))
        inbox = self.answer("GET", f"/api/suggestions?series={sid}&status=pending")
        self.assertEqual((inbox["pending"], inbox["items"][0]["kind"], inbox["items"][0]["source"]), (1, "term", "feedback"))
        suggestion = inbox["items"][0]
        self.assertEqual(suggestion["payload"]["target"], "Má")
        self.assertTrue(suggestion["evidence"])

        base = f"/api/suggestions/{suggestion['id']}"
        self.answer("POST", f"{base}/accept", {"dry_run": True})
        self.answer("POST", "/api/suggestions/bulk", {"series_id": sid, "dry_run": True})
        accepted = self.answer("POST", f"{base}/accept", {"payload": {**suggestion["payload"], "locked": True}})
        self.assertTrue(accepted["suggestion"]["edited"])
        self.answer("POST", f"{base}/undo", {"dry_run": True})
        undone = self.answer("POST", f"{base}/undo", {})
        self.assertEqual(undone["suggestion"]["status"], "pending")
        bulk = self.answer("POST", "/api/suggestions/bulk", {"series_id": sid, "expect": 1})
        self.assertEqual((bulk["count"], bulk["failed"]), (1, []))
        self.answer("POST", f"{base}/undo", {})
        rejected = self.answer("POST", f"{base}/reject", {})
        self.assertEqual(rejected["suggestion"]["status"], "rejected")
        self.assertEqual(self.answer("GET", "/api/suggestions?status=rejected")["counts"]["rejected"], 1)


class InsightsAnswers(Contract):
    edit_demo = True

    def test_insights_routes(self):
        sid = next(row["id"] for row in self.answer("GET", "/api/library/series")["items"] if row["title"] == "Edit Demo")
        unit = self.answer("GET", f"/api/library/series/{sid}/units")["units"][0]["id"]
        self.assertEqual(self.answer("GET", f"/api/series/{sid}/golden?lang=vi")["cues"], [])
        row = self.answer("GET", f"/api/units/{unit}/cues?lang=vi")["rows"][0]
        self.answer("POST", f"/api/units/{unit}/corrections", {"lang": "vi", "kind": "cue_edit", "payload": {"cue": 1, "text": "Má ơi!"}})
        golden = self.answer("GET", f"/api/series/{sid}/golden?lang=vi")
        self.assertEqual([(c["cue"], c["text"], c["source"]) for c in golden["cues"]], [(1, "Má ơi!", row["source"])])

        metrics = self.answer("GET", "/api/metrics?days=7")
        self.assertEqual(metrics["window"]["days"], 7)
        self.assertEqual(len(metrics["daily"]), 7)
        self.assertEqual(metrics["quality"]["cue_edits"]["cues"], 1)


class AnswersOfWhatTheSeededWorkerCannotMake(unittest.TestCase):
    """Answers that need a state or a model the seeded worker has not: they are made by the use cases in-process, the way the
    tests of those use cases make them, and checked against the model of the route."""

    def test_the_full_report_of_insights_with_calls_models_edits_and_verdicts(self):
        from subserver.adapters.store import Store
        from subserver.services.metrics import Metrics
        from tests import test_insights as insights

        provider = {"provider": "gemini", "model": "m-1", "state": "closed", "reason": "", "until": None, "failures": 0, "usable": True}

        class Console(insights.FakeConsole):
            def snapshot(self, limit=100):
                return {**super().snapshot(limit), "providers": [provider]}

        db = Store(":memory:")
        self.addCleanup(db.close)
        insights.Records(db)
        report = Metrics(db, console=Console(), settings_source=lambda: {"max_calls_per_day": 50}, limits=lambda: (10, 0, 0),
                         clock=lambda: insights.NOW).report(30)
        self.assertTrue(report["by_model"] and report["ab"] and report["quality"]["validate"])
        check_answer(Insights, report)

    def test_the_trial_of_a_style(self):
        from tests.test_style_profile import Env

        env = Env(self)
        series = env.translated(1)
        result = env.style.preview(series, {"lang": "vi", "unit_id": env.unit(1), "count": 4, "style": {"tone": "frantic"}})
        check_answer(StylePreview, result)
        self.assertEqual(len(result["rows"]), 4)

    def test_every_kind_of_suggestion_and_every_kind_of_evidence(self):
        base = {
            "id": 1, "series_id": 1, "series": "S", "lang": "vi", "source": "facts", "sources": [], "status": "pending",
            "auto_accepted": False, "decided_at": 0, "created_at": 1.5, "undoable": False, "edited": False,
            "search_display_id": None,
        }
        cue = {"episode": "S01E02", "cue": 12, "timecode": "00:01:02", "text": "Hey", "unit_id": 3, "note": "they switch to casual"}
        for kind, payload in (
            ("term", {"source": "Kyber", "target": "Kyber", "locked": True, "aliases": ["K"], "from": "S01E02", "note": "n"}),
            ("address_rule", {"a": "Zed", "b": "Kyber", "a_to_b": "anh–em", "b_to_a": "em–anh", "from": "S01E02 00:01:02", "until": "S02E01"}),
            ("character", {"name": "Zed", "target": "Zét", "gender": "m", "description": "d", "card": {"name": "Zed", "speech": "dry"}}),
        ):
            with self.subTest(kind=kind):
                check_answer(Suggestion, {**base, "kind": kind, "payload": payload, "evidence": [cue, "a line to check"]})
        check_answer(Suggestion, {**base, "kind": "term", "source": "research", "sources": ["https://example.org/a"],
                                  "payload": {"source": "a", "target": "b"}, "evidence": [{"url": "https://example.org/a"}],
                                  "search_display_id": 4})
        check_answer(SearchSuggestions, {"id": 4, "series_id": 1, "lang": "vi", "html": "<div></div>", "queries": ["q"], "created_at": 1.5})

    def test_the_other_shapes_a_model_may_answer_with(self):
        check_answer(CorrectionAdded, {"ok": True, "scope": "forward", "ids": [], "impact": {"stale_now": 0, "stale_after": 1, "would_stale": 1}, "from": "S01E02"})
        check_answer(UnitContextAnswer, {"applicable": False, "enabled": False})
        check_answer(AdoptAnswer, {"ok": True, "dry_run": True, "adopted": None, "style": {"preset": "", "style": {"tone": "warm"}},
                                   "stale_now": 0, "stale_after": 0, "would_stale": 0})
        check_answer(PresetImpact, {"ok": True, "saved": False, "stale_now": 0, "stale_after": 2, "would_stale": 2, "owners": 1})


class ModelsAreStrict(unittest.TestCase):
    """The check is only worth something if a model refuses what it does not describe."""

    def test_an_answer_with_a_field_the_model_does_not_name_or_a_wrong_type_is_refused(self):
        counts = {"pending": 1, "processing": 0, "deferred": 0, "done": 0, "failed": 0, "cancelled": 0}
        check_answer(QueueCounts, counts)
        with self.assertRaises(ValidationError):
            check_answer(QueueCounts, {**counts, "surprise": 1})
        with self.assertRaises(ValidationError):
            check_answer(QueueCounts, {**counts, "pending": "1"})
        with self.assertRaises(ValidationError):
            check_answer(QueueCounts, {key: value for key, value in counts.items() if key != "done"})

    def test_a_job_row_may_carry_fields_the_console_does_not_read(self):
        check_answer(QueueJob, {"job_id": "a", "title": "", "label": "", "gst_model": "m", "payload_field": 1})


class ZEveryRouteIsDriven(unittest.TestCase):
    """The last test: what the app serves, a model for each JSON route, and a case that called it."""

    def test_every_route_has_a_model_and_a_case(self):
        served = {
            (method, route.path): route for route in APP.routes if route.path.startswith("/api/")
            for method in (route.methods or set()) - {"HEAD"}
        }
        for key in (*NOT_JSON, *NOT_DRIVEN):
            self.assertIn(key, served, f"{key} is excused here but the app has no such route")
        no_model = sorted(key for key, route in served.items() if route.response_model is None and key not in NOT_JSON)
        self.assertEqual(no_model, [], "routes that declare no answer model (api/answers)")
        undriven = sorted(key for key in served if key not in COVERED and key not in NOT_JSON and key not in NOT_DRIVEN)
        self.assertEqual(undriven, [], "routes no case of this file calls")


if __name__ == "__main__":
    unittest.main()
