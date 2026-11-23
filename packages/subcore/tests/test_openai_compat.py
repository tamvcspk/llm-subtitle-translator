"""The ``openai_compat`` adapter against a fake OpenAI-style server on localhost (SPEC criterion 22, FR-9.1-9.3).

No external network: every test talks to a ``ThreadingHTTPServer`` bound to 127.0.0.1 that plays the part of
OpenRouter, Ollama or vLLM and records what it was sent.
"""

from __future__ import annotations

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from subcore import srt
from subcore.providers import (
    AuthError,
    BadOutput,
    Blocked,
    FakeProvider,
    LLMRequest,
    ModelNotFound,
    OpenAICompatProvider,
    ProviderError,
    QuotaExhausted,
    RateLimited,
    Unavailable,
)
from subcore.providers.openai_compat import MODE_NONE, MODE_OBJECT, MODE_SCHEMA, map_http_error, normalize_base_url
from subcore.translate import PromptContext, translate

from .helpers import make_cues, synthetic_srt

KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"


class Reply:
    """What the fake server answers: a status, headers and a body (bytes), or server-sent events."""

    def __init__(self, status=200, body=b"", headers=None, events=None, delay=0.0):
        self.status, self.body, self.headers, self.events, self.delay = status, body, headers or {}, events, delay


def sse(text: str, *, usage=(11, 7), finish="stop", pieces=3) -> Reply:
    """A streamed completion: ``text`` cut into pieces, then the finish reason and (as OpenAI does) the usage."""
    size = max(1, -(-len(text) // pieces))
    chunks = [{"choices": [{"delta": {"content": text[i : i + size]}}]} for i in range(0, len(text), size)]
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    if usage:
        chunks.append({"choices": [], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})
    return Reply(events=[": keep-alive", *[json.dumps(chunk) for chunk in chunks], "[DONE]"])


def plain(text: str, *, usage=(11, 7), finish="stop") -> Reply:
    body = {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }
    return Reply(body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


def error(status: int, message="boom", *, code=None, kind=None, headers=None) -> Reply:
    body = {"error": {"message": message, "code": code, "type": kind}}
    return Reply(status, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})


class Recorded:
    def __init__(self, path: str, headers: dict[str, str], body: dict[str, Any]):
        self.path, self.headers, self.body = path, headers, body


class FakeServer:
    """``script(recorded, call_no)`` returns the :class:`Reply` for each POST."""

    def __init__(self, script: Callable[[Recorded, int], Reply]):
        self.script = script
        self.requests: list[Recorded] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence the test output
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                recorded = Recorded(self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                outer.requests.append(recorded)
                reply = outer.script(recorded, len(outer.requests))
                if reply.delay:
                    threading.Event().wait(reply.delay)
                try:
                    self.send_response(reply.status)
                    if reply.events is not None:
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        for event in reply.events:
                            line = event if event.startswith(":") else f"data: {event}"
                            self.wfile.write(f"{line}\n\n".encode())
                            self.wfile.flush()
                        return
                    for name, value in reply.headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(reply.body)))
                    self.end_headers()
                    self.wfile.write(reply.body)
                except ConnectionError:  # the client gave up (timeout test)
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class ServerCase(unittest.TestCase):
    def setUp(self):
        OpenAICompatProvider.forget_learned_modes()
        self.servers: list[FakeServer] = []

    def tearDown(self):
        for server in self.servers:
            server.close()
        OpenAICompatProvider.forget_learned_modes()

    def serve(self, script) -> FakeServer:
        server = FakeServer(script if callable(script) else (lambda recorded, n: script))
        self.servers.append(server)
        return server

    def provider(self, server: FakeServer, **kwargs) -> OpenAICompatProvider:
        kwargs.setdefault("api_key", KEY)
        return OpenAICompatProvider(server.url, model=kwargs.pop("model", "m-1"), **kwargs)


REQ = LLMRequest(system="sys", user="usr", response_schema={"type": "array"}, json_output=True)


def echoed(recorded: Recorded) -> list[dict[str, Any]]:
    """What a "model" that translates every SOURCE ITEM of the prompt like the fake provider does would answer."""
    user = recorded.body["messages"][1]["content"]
    block = user.split("SOURCE ITEMS (one JSON object per line):\n", 1)[1].split("\n\n", 1)[0]
    return [{"i": item["i"], "text": f"[vi] {item['text']}"} for item in map(json.loads, block.splitlines())]


def echo_script(recorded: Recorded, call_no: int) -> Reply:
    return sse(json.dumps(echoed(recorded), ensure_ascii=False))


class Criterion22aSameResultAsFake(ServerCase):
    """22a: the same fixture through ``fake`` and ``openai_compat`` ends in the same validated subtitle."""

    def test_synthetic_files_translate_the_same_way(self):
        server = self.serve(echo_script)
        for seed in range(5):
            with self.subTest(seed=seed):
                source = srt.parse(synthetic_srt(seed))
                expected = translate(FakeProvider(), source, PromptContext(target_lang="vi"))
                got = translate(self.provider(server), source, PromptContext(target_lang="vi"))
                self.assertEqual(srt.serialize(got.cues), srt.serialize(expected.cues))
                self.assertEqual([c.timing for c in got.cues], [c.timing for c in source])
                self.assertEqual(got.warnings, expected.warnings)

    def test_a_dropped_cue_is_repaired_exactly_as_with_the_fake(self):
        def script(recorded, call_no):
            items = echoed(recorded)
            if call_no == 1:  # the first answer forgets cue 2
                items = [item for item in items if item["i"] != 2]
            return sse(json.dumps(items))

        server = self.serve(script)
        result = translate(self.provider(server), make_cues(["one", "two", "three"]), PromptContext(target_lang="vi"))
        self.assertEqual([c.text for c in result.cues], ["[vi] one", "[vi] two", "[vi] three"])
        self.assertEqual([call.kind for call in result.calls], ["translate", "repair"])
        self.assertEqual(len(server.requests), 2)


class RequestShape(ServerCase):
    def test_what_goes_over_the_wire(self):
        server = self.serve(sse("[]"))
        provider = self.provider(server)
        provider.generate(LLMRequest(system="SYSTEM", user="USER", temperature=0.3))
        sent = server.requests[0]
        self.assertEqual(sent.path, "/v1/chat/completions")
        self.assertEqual(sent.headers["authorization"], f"Bearer {KEY}")  # the stored key, as typed
        self.assertEqual(sent.body["model"], "m-1")
        self.assertEqual(sent.body["messages"], [
            {"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"},
        ])
        self.assertEqual(sent.body["temperature"], 0.3)
        self.assertTrue(sent.body["stream"])
        self.assertEqual(sent.body["stream_options"], {"include_usage": True})
        self.assertNotIn("response_format", sent.body)  # plain text task: no JSON mode

    def test_meta_never_reaches_the_server(self):
        server = self.serve(sse("[]"))
        self.provider(server).generate(LLMRequest(system="s", user="u", meta={"secret-note": "x", "source": {1: "t"}}))
        self.assertNotIn("secret-note", json.dumps(server.requests[0].body))

    def test_there_is_no_web_search_and_a_request_for_one_sends_no_tool(self):
        # FR-7.3: research needs the provider's own search tool; this adapter has none, so the worker never queues it.
        server = self.serve(sse("[]"))
        provider = self.provider(server)
        self.assertFalse(provider.caps.web_search)
        response = provider.generate(LLMRequest(system="s", user="u", web_search=True))
        self.assertNotIn("tools", server.requests[0].body)
        self.assertEqual((response.grounding_sources, response.search_queries, response.cited_text), ((), (), ""))

    def test_a_keyless_local_server_gets_no_authorization_header(self):
        server = self.serve(sse("[]"))
        OpenAICompatProvider(server.url, "", "llama3").generate(REQ)
        self.assertNotIn("authorization", server.requests[0].headers)

    def test_base_url_may_end_in_the_endpoint_or_a_slash(self):
        server = self.serve(sse("[]"))
        for suffix in ("/", "/chat/completions"):
            OpenAICompatProvider(server.url + suffix, "", "m").generate(REQ)
        self.assertEqual({r.path for r in server.requests}, {"/v1/chat/completions"})

    def test_bad_base_urls_are_refused_before_any_call(self):
        for bad in ("", "localhost:11434/v1", "ftp://host/v1", "https://user:pw@host/v1"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                normalize_base_url(bad)
        with self.assertRaises(ValueError):
            OpenAICompatProvider("http://localhost/v1", "", "")  # a model is required

    def test_the_key_is_not_in_repr_nor_in_a_refusal(self):
        server = self.serve(error(401, "bad key"))
        provider = self.provider(server)
        self.assertNotIn(KEY, repr(provider))
        self.assertNotIn("127.0.0.1", repr(provider))
        with self.assertRaises(AuthError) as caught:
            provider.generate(REQ)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn("127.0.0.1", str(caught.exception))


class Criterion22bTypedErrors(ServerCase):
    def raises(self, reply: Reply, kind: type[ProviderError]) -> ProviderError:
        server = self.serve(reply)
        with self.assertRaises(kind) as caught:
            self.provider(server).generate(REQ)
        self.assertIs(type(caught.exception), kind)
        return caught.exception

    def test_401_and_403_are_auth_errors(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertEqual(self.raises(error(status), AuthError).http_status, status)

    def test_an_unknown_model_is_model_not_found_not_an_auth_error(self):
        exc = self.raises(error(404, "model 'x' not found"), ModelNotFound)
        self.assertNotIsInstance(exc, AuthError)
        self.assertEqual(exc.http_status, 404)

    def test_429_is_rate_limited_with_the_retry_after_header(self):
        exc = self.raises(error(429, "slow down", headers={"Retry-After": "7"}), RateLimited)
        self.assertEqual((exc.retry_after, exc.http_status), (7.0, 429))

    def test_429_without_a_hint_is_still_rate_limited(self):
        self.assertIsNone(self.raises(error(429), RateLimited).retry_after)

    def test_a_spent_quota_is_quota_exhausted_by_its_code_not_its_words(self):
        self.raises(error(429, "whatever", code="insufficient_quota"), QuotaExhausted)
        self.raises(error(429, "whatever", kind="insufficient_quota"), QuotaExhausted)
        self.raises(error(402, "no credits"), QuotaExhausted)  # OpenRouter
        # Words that merely sound like a quota are not enough: only the typed code decides.
        self.raises(error(429, "daily quota exceeded"), RateLimited)

    def test_server_errors_are_unavailable(self):
        for status in (500, 502, 503, 504, 529):
            with self.subTest(status=status):
                self.assertEqual(self.raises(error(status), Unavailable).http_status, status)

    def test_a_timeout_is_unavailable(self):
        server = self.serve(Reply(body=b"{}", delay=1.5))
        with self.assertRaises(Unavailable):
            OpenAICompatProvider(server.url, "", "m", timeout_s=0.3).generate(REQ)

    def test_a_refused_connection_is_unavailable(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(Unavailable):
            OpenAICompatProvider(f"http://127.0.0.1:{port}/v1", "", "m", timeout_s=2).generate(REQ)

    def test_an_answer_that_is_not_json_is_bad_output(self):
        self.raises(Reply(body=b"<html>hi</html>", headers={"Content-Type": "text/html"}), BadOutput)

    def test_an_empty_answer_is_bad_output_with_its_finish_reason(self):
        for reply in (plain("", finish="length"), sse("", finish="length")):
            with self.subTest(streamed=reply.events is not None):
                self.assertIn("length", str(self.raises(reply, BadOutput)))

    def test_an_answer_the_content_filter_refused_is_blocked_a_kind_of_bad_output(self):
        for reply in (plain("", finish="content_filter"), sse("", finish="content_filter")):
            with self.subTest(streamed=reply.events is not None):
                self.assertIn("content_filter", str(self.raises(reply, Blocked)))
                self.assertTrue(issubclass(Blocked, BadOutput))

    def test_an_error_inside_the_event_stream_is_typed_too(self):
        stream = Reply(events=[json.dumps({"error": {"code": 502, "message": "upstream died"}})])
        self.raises(stream, Unavailable)
        self.raises(Reply(events=[json.dumps({"error": {"message": "no code at all"}})]), Unavailable)
        limited = Reply(events=[json.dumps({"error": {"code": 429, "message": "slow"}})])
        self.raises(limited, RateLimited)

    def test_other_client_errors_are_plain_provider_errors(self):
        exc = self.raises(error(413, "too big"), ProviderError)
        self.assertEqual(exc.http_status, 413)

    def test_a_redirect_is_not_followed_so_the_key_stays_home(self):
        elsewhere = self.serve(sse("[]"))
        server = self.serve(Reply(302, headers={"Location": elsewhere.url + "/chat/completions"}))
        with self.assertRaises(ProviderError) as caught:
            self.provider(server).generate(REQ)
        self.assertEqual(caught.exception.http_status, 302)
        self.assertEqual(elsewhere.requests, [])

    def test_the_mapping_is_pure(self):
        self.assertIsInstance(map_http_error(401), AuthError)
        self.assertIsInstance(map_http_error(None), ProviderError)
        self.assertIsInstance(map_http_error(429, {"error": "plain string"}), RateLimited)
        self.assertIsInstance(map_http_error(503, "not even a dict"), Unavailable)


class Replies(ServerCase):
    def test_a_streamed_reply_is_joined_and_its_usage_read(self):
        server = self.serve(sse('[{"i":1,"text":"x"}]', usage=(100, 40), pieces=5))
        response = self.provider(server).generate(REQ)
        self.assertEqual(response.text, '[{"i":1,"text":"x"}]')
        self.assertEqual((response.tokens_in, response.tokens_out, response.finish_reason), (100, 40, "stop"))
        self.assertEqual(response.model, "m-1")

    def test_a_server_that_ignores_stream_is_read_as_one_body(self):
        server = self.serve(plain('[{"i":1,"text":"x"}]', usage=(9, 3)))
        response = self.provider(server).generate(REQ)
        self.assertEqual((response.text, response.tokens_in, response.tokens_out), ('[{"i":1,"text":"x"}]', 9, 3))

    def test_content_given_as_parts_is_joined(self):
        body = {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]}
        server = self.serve(Reply(body=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
        self.assertEqual(self.provider(server).generate(REQ).text, "ab")

    def test_a_cut_off_answer_is_returned_for_partial_recovery(self):
        server = self.serve(sse('[{"i":1,"text":"a"},{"i":2', finish="length"))
        response = self.provider(server).generate(REQ)
        self.assertEqual(response.finish_reason, "length")
        self.assertTrue(response.text.endswith('{"i":2'))

    def test_count_tokens_is_an_estimate_without_a_call(self):
        server = self.serve(sse("[]"))
        self.assertGreater(self.provider(server).count_tokens(REQ), 0)
        self.assertEqual(server.requests, [])


class Criterion22cStructuredOutputDegrades(ServerCase):
    """22c: a server without structured output still translates, through JSON mode, then through the prompt alone."""

    @staticmethod
    def refuse_formats(*refused: str):
        """A server that answers 400 to the given ``response_format`` types and translates otherwise."""
        def script(recorded, call_no):
            fmt = recorded.body.get("response_format")
            if fmt is not None and fmt["type"] in refused:
                return error(400, "response_format is not supported", code="invalid_request")
            return echo_script(recorded, call_no)
        return script

    def test_a_schema_the_server_refuses_falls_back_to_json_mode(self):
        server = self.serve(self.refuse_formats("json_schema"))
        provider = self.provider(server)
        self.assertEqual((provider.json_mode, provider.caps.structured_output), (MODE_SCHEMA, True))
        result = translate(provider, make_cues(["one", "two"]), PromptContext(target_lang="vi"))
        self.assertEqual([c.text for c in result.cues], ["[vi] one", "[vi] two"])
        formats = [r.body.get("response_format", {}).get("type") for r in server.requests]
        self.assertEqual(formats, ["json_schema", "json_object"])  # one refusal, then one working call
        self.assertEqual((provider.json_mode, provider.caps.structured_output), (MODE_OBJECT, False))
        # JSON mode wants an object: the model is told where to put the list.
        self.assertIn('"items"', server.requests[1].body["messages"][0]["content"])

    def test_a_server_with_no_response_format_at_all_is_asked_by_the_prompt_alone(self):
        server = self.serve(self.refuse_formats("json_schema", "json_object"))
        provider = self.provider(server)
        result = translate(provider, make_cues(["one", "two"]), PromptContext(target_lang="vi"))
        self.assertEqual([c.text for c in result.cues], ["[vi] one", "[vi] two"])
        formats = [r.body.get("response_format", {}).get("type") for r in server.requests]
        self.assertEqual(formats, ["json_schema", "json_object", None])
        self.assertEqual(provider.json_mode, MODE_NONE)
        self.assertNotIn("response_format", server.requests[-1].body)

    def test_the_engine_stops_sending_a_schema_once_the_capability_is_gone(self):
        server = self.serve(self.refuse_formats("json_schema"))
        provider = self.provider(server)
        translate(provider, make_cues(["a", "b"]), PromptContext(target_lang="vi"))
        before = len(server.requests)
        translate(provider, make_cues(["c", "d"]), PromptContext(target_lang="vi"))
        later = [r.body.get("response_format", {}).get("type") for r in server.requests[before:]]
        self.assertEqual(later, ["json_object"])  # no schema attempt, no second refusal

    def test_a_later_provider_for_the_same_server_and_model_starts_where_the_last_one_ended(self):
        server = self.serve(self.refuse_formats("json_schema"))
        translate(self.provider(server), make_cues(["a"]), PromptContext(target_lang="vi"))
        before = len(server.requests)
        translate(self.provider(server, api_key="another-key"), make_cues(["b"]), PromptContext(target_lang="vi"))
        self.assertEqual(len(server.requests) - before, 1)  # the refusal was learned, not paid for again

    def test_a_schema_capable_server_gets_the_array_wrapped_in_an_object(self):
        server = self.serve(echo_script)
        translate(self.provider(server), make_cues(["a"]), PromptContext(target_lang="vi"))
        fmt = server.requests[0].body["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        schema = fmt["json_schema"]["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["properties"]["items"]["type"], "array")

    def test_an_object_answer_is_read_like_a_list(self):
        def script(recorded, call_no):
            return sse(json.dumps({"items": [{"i": 1, "text": "[vi] a"}]}))

        result = translate(self.provider(self.serve(script)), make_cues(["a"]), PromptContext(target_lang="vi"))
        self.assertEqual([c.text for c in result.cues], ["[vi] a"])

    def test_other_400s_are_not_mistaken_for_a_refused_format(self):
        server = self.serve(error(400, "context length exceeded", code="context_length_exceeded"))
        provider = self.provider(server)
        with self.assertRaises(ProviderError) as caught:
            provider.generate(REQ)
        self.assertEqual(caught.exception.http_status, 400)
        # It was retried at every weaker format (it cannot tell), but it ends as the typed error, never a loop.
        self.assertLessEqual(len(server.requests), 3)

    def test_a_plain_text_task_never_asks_for_json(self):
        server = self.serve(sse("a summary"))
        self.provider(server).generate(LLMRequest(system="s", user="u", task="summarize"))
        self.assertNotIn("response_format", server.requests[0].body)

    def test_a_caller_may_start_in_a_weaker_mode(self):
        server = self.serve(sse("{}"))
        provider = self.provider(server, mode=MODE_OBJECT)
        self.assertFalse(provider.caps.structured_output)
        provider.generate(REQ)
        self.assertEqual(server.requests[0].body["response_format"], {"type": "json_object"})
        with self.assertRaises(ValueError):
            self.provider(server, mode="xml")


class Criterion22dTaskModels(ServerCase):
    def test_the_provider_asks_for_the_model_it_was_built_with(self):
        server = self.serve(sse("[]"))
        for task, model in (("translate", "big"), ("extract", "cheap"), ("summarize", "cheap"), ("research", "search")):
            self.provider(server, model=model).generate(LLMRequest(system="s", user="u", task=task))
        self.assertEqual([r.body["model"] for r in server.requests], ["big", "cheap", "cheap", "search"])


if __name__ == "__main__":
    unittest.main()
