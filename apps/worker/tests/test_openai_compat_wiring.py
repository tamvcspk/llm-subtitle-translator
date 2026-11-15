"""P7 (FR-9.1-9.3, ADR-3): the worker runs on a second provider. SPEC section 6, criterion 22 d and e.

The adapter itself is covered in ``packages/subcore/tests/test_openai_compat.py`` (22 a-c); here the worker's wiring
is: which model a task gets, how the provider is chosen and rebuilt when the settings change, where the key comes
from, and that it never leaks. The "model server" is a fake on 127.0.0.1; nothing leaves the machine.
"""

import contextlib
import json
import logging
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from subcore import srt
from subcore.providers import AuthError, OpenAICompatProvider, ResilientProvider

from gst_worker.bodies import BodyLog
from gst_worker.config import model_for_task, normalize_app_config, provider_name
from gst_worker.connection_tests import test_connection
from gst_worker.gemini import openai_models
from gst_worker.llm import KeyedProvider, LiveProviderFactory, build_provider, rate_limits
from gst_worker.ports import ApiKey, MissingApiKey
from gst_worker.redaction import DEFAULT
from gst_worker.settings import SettingsService
from gst_worker.store import Store
from gst_worker.telemetry import Telemetry
from gst_worker.translation_attempt import TranslationAttempt

KEY = "sk-or-v1-5f1c9d0a7e2b4c6d8e0f1a2b3c4d5e6f"
OTHER_KEY = "sk-or-v1-aaaaaaaabbbbbbbbccccccccdddddddd"
SOURCE = "1\n00:00:01,000 --> 00:00:02,000\nHello <i>there</i>\n\n2\n00:00:03,000 --> 00:00:04,500\nHow are you?\n"


class ModelServer:
    """A tiny OpenAI-style server. ``handler(recorded_body, headers, call_no)`` returns ``(status, body_dict)``;
    the default one translates every SOURCE ITEM of the prompt."""

    def __init__(self, handler=None, models=("m-big", "m-cheap")):
        self.handler = handler or self.translate
        self.models = models
        self.requests: list[dict] = []  # {"path", "headers", "body"}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.requests.append({"path": self.path, "headers": headers, "body": None})
                if outer.requires_key and headers.get("authorization") != f"Bearer {outer.requires_key}":
                    return self._send(401, {"error": {"message": "no key"}})
                self._send(200, {"data": [{"id": m} for m in outer.models]})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.requests.append({"path": self.path, "headers": headers, "body": body})
                status, payload = outer.handler(body, headers, len(outer.requests))
                self._send(status, payload)

        self.requires_key = ""
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    @staticmethod
    def translate(body, headers, call_no):
        user = body["messages"][1]["content"]
        block = user.split("SOURCE ITEMS (one JSON object per line):\n", 1)[1].split("\n\n", 1)[0]
        items = [{"i": it["i"], "text": f"[vi] {it['text']}"} for it in map(json.loads, block.splitlines())]
        return 200, {
            "choices": [{"message": {"content": json.dumps(items)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def models_of_calls(self):
        return [r["body"]["model"] for r in self.requests if r["body"]]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@contextlib.contextmanager
def service_over(store_dir: Path, env=None):
    store = Store(store_dir / "app.db")
    try:
        service = SettingsService(
            store, config_path=str(store_dir / "config.json"), postprocess_targets_path=str(store_dir / "targets.json"),
            env={"BAZARR_CONFIG_PATH": str(store_dir / "none.yaml"), **(env or {})},
        )
        service.start()
        yield service, store
    finally:
        store.close()


class TaskModelTests(unittest.TestCase):
    """22d: the model follows the task (FR-9.3)."""

    SETTINGS = {"gst_model": "big", "memory_model": "cheap", "summarize_model": "tiny", "research_model": "search"}

    def test_each_task_has_its_own_setting(self):
        for task, model in (("translate", "big"), ("extract", "cheap"), ("summarize", "tiny"), ("research", "search")):
            self.assertEqual(model_for_task(self.SETTINGS, task), model, task)

    def test_a_task_without_a_setting_falls_back_down_the_chain(self):
        only_main = {"gst_model": "big"}
        for task in ("translate", "extract", "summarize", "research"):
            self.assertEqual(model_for_task(only_main, task), "big", task)
        self.assertEqual(model_for_task({"gst_model": "big", "memory_model": "cheap"}, "summarize"), "cheap")
        self.assertEqual(model_for_task({"gst_model": "big", "memory_model": "cheap"}, "research"), "big")

    def test_blank_and_missing_settings_use_the_given_default(self):
        self.assertEqual(model_for_task({"gst_model": "  "}, "extract", "fallback"), "fallback")
        self.assertEqual(model_for_task({}, "translate", "fallback"), "fallback")

    def test_the_attempt_asks_the_factory_for_the_model_of_each_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Show.S01E01.en.srt"
            source.write_text(SOURCE, encoding="utf-8")
            asked = []

            def factory(settings, model, *, deadline=None, on_call=None):
                asked.append(model)
                from subcore.providers import FakeProvider
                return FakeProvider(model=model)

            attempt = TranslationAttempt(provider_factory=factory)
            attempt.run(
                {"job_id": "j", "subtitle_path": str(source), "target_code": "vi", "output_path": str(root / "out.srt")},
                "", {"gst_model": "big", "memory_model": "cheap", "summarize_model": "tiny"},
            )
            self.assertEqual(asked, ["big"])  # memory is off: only the translation model was needed


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_gemini_stays_the_default_and_nothing_else_changes(self):
        with service_over(self.root) as (service, store):
            settings = service.settings()
            self.assertEqual((settings["llm_provider"], settings["llm_base_url"]), ("gemini", ""))
            self.assertEqual(provider_name(settings), "gemini")
            self.assertEqual(rate_limits(settings)[0], 10)  # the free-tier pacing still applies to Gemini

    def test_provider_and_address_follow_stored_then_environment_then_default(self):
        env = {"LLM_PROVIDER": "openai_compat", "LLM_BASE_URL": "http://env-server:11434/v1/"}
        with service_over(self.root, env) as (service, store):
            self.assertEqual(service.settings()["llm_provider"], "openai_compat")
            self.assertEqual(service.settings()["llm_base_url"], "http://env-server:11434/v1")
            service.save({"llm_provider": "gemini", "llm_base_url": "https://openrouter.ai/api/v1/"})
            self.assertEqual(service.settings()["llm_provider"], "gemini")
            self.assertEqual(service.settings()["llm_base_url"], "https://openrouter.ai/api/v1")

    def test_an_unknown_provider_name_means_gemini(self):
        self.assertEqual(normalize_app_config({"llm_provider": "openai"})["llm_provider"], "gemini")
        self.assertEqual(normalize_app_config({"llm_provider": " OpenAI_Compat "})["llm_provider"], "openai_compat")
        with service_over(self.root, {"LLM_PROVIDER": "skynet"}) as (service, store):
            self.assertEqual(service.settings()["llm_provider"], "gemini")

    def test_token_limits_are_clamped(self):
        config = normalize_app_config({"llm_max_input_tokens": "5", "llm_max_output_tokens": "x"})
        self.assertEqual((config["llm_max_input_tokens"], config["llm_max_output_tokens"]), (1000, 8192))

    def test_a_key_of_the_second_provider_is_stored_and_seeded_like_the_others(self):
        with service_over(self.root, {"LLM_API_KEY": KEY}) as (service, store):
            self.assertEqual([k.provider for k in store.api_keys()], ["openai_compat"])
            self.assertEqual(service.settings()["openai_compat_api_key"], KEY)
            added = service.add_key({"provider": "openai_compat", "value": OTHER_KEY, "label": "spare"})
            self.assertEqual(added["last4"], OTHER_KEY[-4:])
            self.assertNotIn(KEY, json.dumps(service.public()) + json.dumps(service.list_keys()))

    def test_the_key_slot_of_the_second_provider_works_through_the_settings_form(self):
        with service_over(self.root) as (service, store):
            service.save({"openai_compat_api_key": KEY})
            self.assertEqual(store.api_keys("openai_compat")[0].value, KEY)
            self.assertEqual(service.public()["openai_compat_api_key_last4"], KEY[-4:])
            self.assertNotIn(KEY, json.dumps(service.public()))

    def test_backups_carry_the_settings_but_no_key(self):
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat", "llm_base_url": "http://x/v1", "openai_compat_api_key": KEY})
            exported = service.export_config()
            self.assertEqual((exported["llm_provider"], exported["llm_base_url"]), ("openai_compat", "http://x/v1"))
            self.assertNotIn(KEY, json.dumps(exported))

    def test_an_unconfigured_server_is_a_warning(self):
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat"})
            self.assertEqual([w["kind"] for w in service.warnings()], ["provider-unconfigured"])
            service.save({"llm_base_url": "http://x/v1"})
            self.assertEqual(service.warnings(), [])

    def test_a_model_the_server_does_not_know_warns_for_any_task_model(self):
        with service_over(self.root) as (service, store):
            service.save({"summarize_model": "gone-1", "research_model": "gone-2"})
            service.model_not_found("gone-1", "no such model")
            service.model_not_found("gone-2", "no such model")
            self.assertEqual({w["model"] for w in service.warnings()}, {"gone-1", "gone-2"})


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.server = ModelServer()
        self.addCleanup(self.server.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def settings(self, **extra):
        return {"llm_provider": "openai_compat", "llm_base_url": self.server.url, **extra}

    def test_the_plain_path_builds_an_openai_provider(self):
        provider = build_provider(self.settings(openai_compat_api_key=KEY), "m-big")
        self.assertIsInstance(provider, ResilientProvider)
        self.assertIsInstance(provider.inner, OpenAICompatProvider)
        self.assertEqual((provider.name, provider.model), ("openai_compat", "m-big"))
        self.assertEqual(provider.caps.max_output_tokens, 8192)

    def test_the_token_limits_of_the_settings_become_the_capabilities(self):
        provider = build_provider(self.settings(llm_max_input_tokens=32000, llm_max_output_tokens=4096), "m")
        self.assertEqual((provider.caps.max_input_tokens, provider.caps.max_output_tokens), (32000, 4096))

    def test_no_address_is_refused_before_any_call(self):
        with self.assertRaises(MissingApiKey) as caught:
            build_provider({"llm_provider": "openai_compat"}, "m")
        self.assertIn("base URL", str(caught.exception))
        with self.assertRaises(MissingApiKey):
            build_provider({"llm_provider": "openai_compat", "llm_base_url": "localhost:11434"}, "m")

    def test_a_server_of_your_own_needs_no_key_and_is_not_paced_like_the_free_tier(self):
        self.assertEqual(rate_limits(self.settings())[0], 0)
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url})
            provider = LiveProviderFactory(service)(service.settings(), "m-cheap")
            provider.generate(_request())
            self.assertNotIn("authorization", self.server.requests[0]["headers"])

    def test_without_a_key_for_gemini_it_still_refuses(self):
        with service_over(self.root) as (service, store):
            with self.assertRaises(MissingApiKey):
                LiveProviderFactory(service)(service.settings(), "m")

    def test_a_refused_key_is_marked_invalid_and_the_next_one_takes_over(self):
        def handler(body, headers, call_no):
            if headers.get("authorization") == f"Bearer {KEY}":
                return 401, {"error": {"message": "bad key"}}
            return ModelServer.translate(body, headers, call_no)

        self.server.handler = handler
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url})
            service.add_key({"provider": "openai_compat", "value": KEY})
            service.add_key({"provider": "openai_compat", "value": OTHER_KEY})
            provider = LiveProviderFactory(service)(service.settings(), "m-big")
            response = provider.generate(_request())
            self.assertIn("[vi]", response.text)
            self.assertEqual([k.status for k in store.api_keys("openai_compat")], ["invalid", "ok"])
            self.assertEqual(
                [r["headers"]["authorization"] for r in self.server.requests],
                [f"Bearer {KEY}", f"Bearer {OTHER_KEY}"],
            )

    def test_every_key_refused_says_so(self):
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url, "openai_compat_api_key": KEY})
            service.key_refused(store.api_keys("openai_compat")[0].id)
            with self.assertRaises(MissingApiKey) as caught:
                LiveProviderFactory(service)(service.settings(), "m")
            self.assertIn("refused", str(caught.exception))
            self.assertNotIn(KEY, str(caught.exception))

    def test_a_refusal_from_a_keyless_server_marks_nothing(self):
        self.server.handler = lambda body, headers, n: (401, {"error": {"message": "who are you"}})
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url})
            with self.assertRaises(AuthError):
                LiveProviderFactory(service)(service.settings(), "m").generate(_request())
            self.assertEqual(store.api_keys(), [])

    def test_the_key_and_the_address_are_live_without_a_restart(self):
        other = ModelServer()
        self.addCleanup(other.close)
        with service_over(self.root) as (service, store):
            factory = LiveProviderFactory(service)
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url, "openai_compat_api_key": KEY})
            factory(service.settings(), "m-big").generate(_request())
            service.save({"openai_compat_api_key": OTHER_KEY})  # a new key, the same slot
            factory(service.settings(), "m-big").generate(_request())
            service.save({"llm_base_url": other.url})  # a new address
            factory(service.settings(), "m-big").generate(_request())
            self.assertEqual(
                [r["headers"]["authorization"] for r in self.server.requests], [f"Bearer {KEY}", f"Bearer {OTHER_KEY}"]
            )
            self.assertEqual(len(other.requests), 1)
            self.assertEqual(other.requests[0]["headers"]["authorization"], f"Bearer {OTHER_KEY}")

    def test_switching_the_provider_back_is_live_too(self):
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url})
            self.assertEqual(LiveProviderFactory(service)(service.settings(), "m").name, "openai_compat")
            service.save({"llm_provider": "gemini", "gemini_api_key": "AIzaSyAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1111"})
            self.assertEqual(LiveProviderFactory(service)(service.settings(), "m").name, "gemini")

    def test_the_ledger_hears_about_the_model_the_server_does_not_know(self):
        self.server.handler = lambda body, headers, n: (404, {"error": {"message": "model not found"}})
        with service_over(self.root) as (service, store):
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url, "openai_compat_api_key": KEY})
            provider = LiveProviderFactory(service)(service.settings(), "ghost")
            self.assertIsInstance(provider.inner, KeyedProvider)
            with self.assertRaises(Exception):
                provider.generate(_request())
            self.assertEqual(store.api_keys("openai_compat")[0].status, "ok")  # a wrong model is not a wrong key
            self.assertIn("ghost", service.model_warnings)


def _request():
    from subcore.providers import LLMRequest

    return LLMRequest(
        system="s", user="SOURCE ITEMS (one JSON object per line):\n" + json.dumps({"i": 1, "text": "hi"}),
        meta={"targets": [1], "source": {1: "hi"}},
    )


class ConnectionAndModelListTests(unittest.TestCase):
    class Http:
        """The worker's HTTP client over urllib, minimally: enough to reach the fake server."""

        def get_json(self, url, params=None, headers=None):
            import urllib.request

            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read())

    def setUp(self):
        self.server = ModelServer()
        self.addCleanup(self.server.close)

    def test_a_key_is_tested_by_listing_the_models(self):
        self.server.requires_key = KEY
        ok = test_connection("openai_compat_api_key", {"llm_base_url": self.server.url, "openai_compat_api_key": KEY}, self.Http())
        self.assertTrue(ok["ok"], ok)
        self.assertNotIn(KEY, json.dumps(ok))
        refused = test_connection(
            "openai_compat_api_key", {"llm_base_url": self.server.url, "openai_compat_api_key": OTHER_KEY}, self.Http()
        )
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["auth_failed"])  # a bad key, not an unreachable server
        self.assertNotIn(OTHER_KEY, json.dumps(refused))

    def test_a_missing_or_wrong_address_is_said_plainly(self):
        result = test_connection("openai_compat_api_key", {"llm_base_url": ""}, self.Http())
        self.assertFalse(result["ok"])
        self.assertNotIn("auth_failed", result)
        self.assertIn("base URL", result["message"])

    def test_the_model_list_comes_from_the_server(self):
        self.assertEqual([m["id"] for m in openai_models(self.Http(), {"llm_base_url": self.server.url})], ["m-big", "m-cheap"])
        self.assertEqual(openai_models(self.Http(), {"llm_base_url": ""}), [])
        self.assertEqual(openai_models(self.Http(), {"llm_base_url": "http://127.0.0.1:1/v1"}), [])

    def test_testing_the_stored_key_settles_its_status(self):
        with tempfile.TemporaryDirectory() as tmp, service_over(Path(tmp)) as (service, store):
            self.server.requires_key = KEY
            service.save({"llm_provider": "openai_compat", "llm_base_url": self.server.url, "openai_compat_api_key": KEY})
            key_id = store.api_keys("openai_compat")[0].id
            service.key_refused(key_id)
            result = service.test_key(key_id, self.Http())
            self.assertTrue(result["ok"], result)
            self.assertEqual(store.api_keys("openai_compat")[0].status, "ok")
            self.assertEqual(self.server.requests[0]["path"], "/v1/models")


class Criterion22eKeyNeverLeaks(unittest.TestCase):
    """22e: the key comes from ``api_keys`` and appears in no log, event, call record or prompt body."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.server = ModelServer()
        self.addCleanup(self.server.close)

    def run_job(self, service, store, telemetry):
        source = self.root / "Show.S01E01.en.srt"
        source.write_text(SOURCE, encoding="utf-8")
        attempt = TranslationAttempt(
            usage=store, provider_factory=LiveProviderFactory(service), telemetry=telemetry,
        )
        job = {"job_id": "job-1", "subtitle_path": str(source), "target_code": "vi", "target_language": "Vietnamese",
               "output_path": str(self.root / "Show.S01E01.vi.srt")}
        return attempt.run(job, "A show.", service.settings())

    def everything_written(self, store, bodies_dir: Path) -> str:
        parts = [json.dumps([vars(e) for e in store.events(limit=1000)], default=str)]
        parts.append(json.dumps([vars(c) for c in store.llm_calls()], default=str))
        for path in bodies_dir.rglob("*"):
            if path.is_file():
                import gzip

                raw = path.read_bytes()
                parts.append((gzip.decompress(raw) if path.suffix == ".gz" else raw).decode("utf-8", "replace"))
        return "\n".join(parts)

    def test_the_key_of_the_database_is_sent_and_nothing_written_holds_it(self):
        with service_over(self.root / "state") as (service, store):
            service.save({
                "llm_provider": "openai_compat", "llm_base_url": self.server.url, "gst_model": "m-big",
                "openai_compat_api_key": KEY, "log_prompt_bodies": "all",
            })
            bodies = BodyLog(self.root / "state" / "logs" / "llm")
            telemetry = Telemetry(store, usage=store, bodies=bodies, settings_source=service.settings, synchronous=True)
            telemetry.apply_settings(service.settings())
            stream = _CaptureLogs()
            with stream:
                outcome = self.run_job(service, store, telemetry)
            telemetry.close()

            self.assertEqual(outcome, "translated")
            self.assertEqual(self.server.requests[0]["headers"]["authorization"], f"Bearer {KEY}")  # from api_keys
            self.assertEqual(self.server.models_of_calls(), ["m-big"])
            result = srt.parse((self.root / "Show.S01E01.vi.srt").read_text(encoding="utf-8"))
            self.assertEqual(result[1].text, "[vi] How are you?")
            written = self.everything_written(store, self.root / "state" / "logs" / "llm")
            self.assertIn("openai_compat", written)  # the calls were recorded...
            self.assertNotIn(KEY, written)  # ...without the key
            self.assertNotIn(KEY, stream.text())

    def test_a_server_that_echoes_the_key_in_its_error_is_redacted_everywhere(self):
        self.server.handler = lambda body, headers, n: (
            401, {"error": {"message": f"Invalid token {headers['authorization']} for this endpoint"}}
        )
        with service_over(self.root / "state") as (service, store):
            service.save({
                "llm_provider": "openai_compat", "llm_base_url": self.server.url, "gst_model": "m-big",
                "openai_compat_api_key": KEY, "log_prompt_bodies": "all",
            })
            bodies = BodyLog(self.root / "state" / "logs" / "llm")
            telemetry = Telemetry(store, usage=store, bodies=bodies, settings_source=service.settings, synchronous=True)
            telemetry.apply_settings(service.settings())
            stream = _CaptureLogs()
            with stream, self.assertRaises(RuntimeError) as caught:
                self.run_job(service, store, telemetry)
            telemetry.close()

            self.assertNotIn(KEY, str(caught.exception))
            self.assertIn("rejected the API key", str(caught.exception))
            self.assertNotIn(KEY, self.everything_written(store, self.root / "state" / "logs" / "llm"))
            self.assertNotIn(KEY, stream.text())
            self.assertEqual(store.api_keys("openai_compat")[0].status, "invalid")  # and the key was condemned

    def test_the_registered_secret_covers_the_second_providers_key(self):
        with service_over(self.root / "state") as (service, store):
            service.save({"openai_compat_api_key": KEY})
            self.assertNotIn(KEY, DEFAULT.redact(f"call failed with {KEY} in it"))


class _CaptureLogs:
    """Everything logged at any level while the block runs, formatted as a handler would write it."""

    def __init__(self):
        self.records: list[logging.LogRecord] = []
        outer = self

        class Handler(logging.Handler):
            def emit(self, record):
                outer.records.append(record)

        self.handler = Handler(level=logging.DEBUG)

    def __enter__(self):
        self.previous = logging.getLogger().level
        logging.getLogger().addHandler(self.handler)
        logging.getLogger().setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        logging.getLogger().removeHandler(self.handler)
        logging.getLogger().setLevel(self.previous)
        return False

    def text(self) -> str:
        from gst_worker.logs import RedactingFormatter

        formatter = RedactingFormatter("%(name)s %(message)s")
        return "\n".join(formatter.format(record) for record in self.records)


if __name__ == "__main__":
    unittest.main()
