"""P16: research grounding (SPEC FR-7, criterion 23). The model is the fake provider; the Gemini shape is the one a live call
returned on 2026-11-23 (redirect links, a domain as the title, the same page in several chunks)."""

import json
import unittest
from types import SimpleNamespace

from subcore.providers import BadOutput, Capabilities, FakeProvider, GroundingSource, LLMRequest, LLMResponse
from subcore.providers.gemini import GeminiProvider, parse_grounding, parse_search_suggestions, source_domain
from subcore.research import MAX_TERMS, ResearchError, research_terms
from subcore.suggest import TermItem

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"

ANSWER = (
    "Theo bản dịch tiếng Việt chính thức:\n"
    "**Himmel the Hero:** Dũng sĩ Himmel\n"
    "**Land of Souls:** Thiên đường Aureole\n"
    "**Fern:** Fern (giữ nguyên tên riêng)"
)

# The "Search Suggestions" chip of the live call: Google's own HTML, not to be touched.
CHIP = '<style>.c{display:flex}</style><div class="c"><a href="https://www.google.com/search?q=frieren">frieren vietnamese</a></div>'

# What `candidates[0].grounding_metadata.model_dump(mode="json", exclude_none=True)` gave, with the long tokens cut short.
GROUNDING = {
    "grounding_chunks": [
        {"web": {"title": "wikipedia.org", "uri": REDIRECT + "AUZ-wiki-one"}},
        {"web": {"title": "wikipedia.org", "uri": REDIRECT + "AUZ-wiki-one"}},  # the same page again
        {"web": {"title": "fandom.com", "uri": REDIRECT + "AUZ-fandom"}},
        {"web": {"title": "wikipedia.org", "uri": REDIRECT + "AUZ-wiki-two"}},  # another page of the same site
        {"web": {"title": "reddit.com", "uri": REDIRECT + "AUZ-reddit"}},
    ],
    "grounding_supports": [
        {"grounding_chunk_indices": [0, 1], "segment": {"end_index": 40, "text": "Theo bản dịch tiếng Việt chính thức:"}},
        {"grounding_chunk_indices": [2, 1], "segment": {"start_index": 41, "text": "**Himmel the Hero:** Dũng sĩ Himmel"}},
        {"grounding_chunk_indices": [3, 4], "segment": {"start_index": 80, "text": "**Land of Souls:** Thiên đường Aureole"}},
    ],
    "web_search_queries": ["Land of Souls Frieren Vietnamese translation", "Frieren Vietnamese official title"],
    "search_entry_point": {"rendered_content": CHIP},
}


def chunk(text, grounding=None, usage=None):
    candidate = SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"), grounding_metadata=grounding)
    return SimpleNamespace(text=text, usage_metadata=usage, candidates=[candidate])


class Client:
    def __init__(self, chunks):
        self.models = SimpleNamespace(generate_content_stream=self._stream)
        self._chunks, self.requests = chunks, []

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        return iter(self._chunks)


class GroundingParseTests(unittest.TestCase):
    def test_pages_are_distinct_and_named_by_the_site_the_title_gives(self):
        sources, queries, _ = parse_grounding(GROUNDING, ANSWER)
        self.assertEqual([s.domain for s in sources], ["wikipedia.org", "fandom.com", "wikipedia.org", "reddit.com"])
        self.assertEqual(len(sources), 4)  # five chunks, one of them a repeat
        self.assertTrue(all(s.url.startswith(REDIRECT) for s in sources))  # the link is Google's: the caller resolves it
        self.assertEqual(queries, ("Land of Souls Frieren Vietnamese translation", "Frieren Vietnamese official title"))

    def test_the_answer_carries_a_mark_after_each_passage_a_page_backs_counting_distinct_pages_from_zero(self):
        _, _, cited = parse_grounding(GROUNDING, ANSWER)
        self.assertIn("**Himmel the Hero:** Dũng sĩ Himmel [0, 1]", cited)  # chunks 2 and 1 are pages 1 and 0
        self.assertIn("**Land of Souls:** Thiên đường Aureole [2, 3]", cited)
        self.assertIn("chính thức: [0]", cited)
        self.assertTrue(cited.endswith("(giữ nguyên tên riêng)"))  # a passage nobody backs has no mark

    def test_a_passage_that_cannot_be_found_is_placed_by_the_providers_byte_offset(self):
        meta = {
            "grounding_chunks": [{"web": {"title": "wikipedia.org", "uri": REDIRECT + "x"}}],
            "grounding_supports": [{"grounding_chunk_indices": [0], "segment": {"end_index": len("Dũng sĩ".encode()), "text": "gone"}}],
        }
        self.assertEqual(parse_grounding(meta, "Dũng sĩ Himmel")[2], "Dũng sĩ [0] Himmel")

    def test_nothing_grounded_gives_nothing(self):
        self.assertEqual(parse_grounding(None, "text"), ((), (), ""))
        self.assertEqual(parse_grounding({"grounding_chunks": [{"retrieved_context": {}}]}, "text"), ((), (), ""))

    def test_the_search_suggestions_html_is_kept_exactly_as_the_provider_sent_it(self):
        self.assertEqual(parse_search_suggestions(GROUNDING), CHIP)
        self.assertEqual(parse_search_suggestions({"search_entry_point": {"rendered_content": "  \n"}}), "")
        self.assertEqual(parse_search_suggestions({"search_entry_point": {"rendered_content": "x" * (64 * 1024 + 1)}}), "")
        self.assertEqual(parse_search_suggestions({"grounding_chunks": []}), "")
        self.assertEqual(parse_search_suggestions(None), "")

    def test_the_domain_comes_from_the_title_then_from_a_real_link_never_from_googles_redirect(self):
        self.assertEqual(source_domain(REDIRECT + "x", "Wikipedia.org"), "wikipedia.org")
        self.assertEqual(source_domain(REDIRECT + "x", "www.fandom.com"), "fandom.com")
        self.assertEqual(source_domain("https://en.wikipedia.org/wiki/Fern", "Fern (Frieren)"), "en.wikipedia.org")
        self.assertEqual(source_domain(REDIRECT + "x", "A page title with spaces"), "")  # nothing says where it is
        self.assertEqual(source_domain("", ""), "")


class GeminiSearchTests(unittest.TestCase):
    def test_a_request_with_web_search_gets_the_search_tool_and_hands_back_its_sources(self):
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=40, thoughts_token_count=0, tool_use_prompt_token_count=900)
        client = Client([chunk(ANSWER[:30]), chunk(ANSWER[30:], GROUNDING, usage)])
        request = LLMRequest(system="s", user="u", task="research", web_search=True)
        response = GeminiProvider("k", "gemini-test", client=client).generate(request)
        tools = client.requests[0]["config"].tools
        self.assertEqual(len(tools), 1)
        self.assertIsNotNone(tools[0].google_search)
        self.assertEqual(response.text, ANSWER)
        self.assertEqual([s.domain for s in response.grounding_sources][:2], ["wikipedia.org", "fandom.com"])
        self.assertEqual(len(response.search_queries), 2)
        self.assertIn("[0, 1]", response.cited_text)
        self.assertEqual(response.tokens_in, 1000)  # what the search brought into the prompt is billed as input
        self.assertEqual(response.search_suggestions, CHIP)

    def test_without_web_search_there_is_no_tool_and_no_sources_even_if_metadata_came(self):
        client = Client([chunk("text", GROUNDING)])
        response = GeminiProvider("k", client=client).generate(LLMRequest(system="s", user="u"))
        self.assertIsNone(client.requests[0]["config"].tools)
        self.assertEqual((response.grounding_sources, response.search_queries, response.cited_text), ((), (), ""))
        self.assertEqual(response.search_suggestions, "")

    def test_a_model_without_web_search_in_its_caps_is_asked_without_the_tool(self):
        client = Client([chunk("text")])
        provider = GeminiProvider("k", caps=Capabilities(web_search=False), client=client)
        provider.generate(LLMRequest(system="s", user="u", web_search=True))
        self.assertIsNone(client.requests[0]["config"].tools)


def items(*names):
    return [TermItem(name, "place", ("S01E01", "S01E02"), ({"episode": "S01E01", "cue": 3, "text": f"line with {name}"},)) for name in names]


def sources(*domains):
    return tuple(GroundingSource(f"{REDIRECT}{d}-{n}", d, d) for n, d in enumerate(domains))


def grounded(text="the answer", domains=("wikipedia.org", "fandom.com", "reddit.com")):
    return LLMResponse(text, grounding_sources=sources(*domains), search_queries=("q",), cited_text=text + " [0]")


class Env:
    """A search provider and a reader, both fakes, and what each was asked."""

    def __init__(self, search_reply, read_reply, *, web_search=True):
        self.search = FakeProvider(lambda req, n: search_reply, caps=Capabilities(web_search=web_search), model="search-1")
        self.read = FakeProvider(lambda req, n: read_reply if isinstance(read_reply, (str, LLMResponse)) else json.dumps(read_reply), model="read-1")


class ResearchTermsTests(unittest.TestCase):
    def test_a_grounded_answer_becomes_drafts_whose_sources_are_the_pages_the_provider_reported(self):
        reply = {"terms": [
            {"i": 0, "target": "Dũng sĩ Himmel", "source_refs": [0, 1], "note": "a person"},
            {"i": 1, "target": "Thiên đường Aureole", "source_refs": [2]},
        ]}
        env = Env(grounded(), reply)
        outcome = research_terms(env.search, env.read, items("Himmel the Hero", "Land of Souls"), lang="vi", title="Frieren")
        self.assertEqual(outcome.calls, 2)
        first, second = outcome.drafts
        self.assertEqual(first.payload, {"source": "Himmel the Hero", "target": "Dũng sĩ Himmel", "note": "a person"})
        self.assertEqual([s.domain for s in first.sources], ["wikipedia.org", "fandom.com"])
        self.assertEqual([s.url for s in second.sources], [f"{REDIRECT}reddit.com-2"])
        self.assertEqual(first.evidence[0]["text"], "line with Himmel the Hero")  # the cues that show the term come along
        self.assertEqual((outcome.pages, outcome.queries, outcome.unanswered), (3, ("q",), ()))

    def test_the_search_suggestions_of_the_search_call_come_back_with_the_outcome_unchanged(self):
        env = Env(LLMResponse("the answer", grounding_sources=sources("wikipedia.org"), cited_text="the answer [0]", search_suggestions=CHIP),
                  {"terms": [{"i": 0, "target": "Dũng sĩ Himmel", "source_refs": [0]}]})
        self.assertEqual(research_terms(env.search, env.read, items("Himmel"), lang="vi").search_suggestions, CHIP)

    def test_the_first_call_searches_without_a_schema_the_second_reads_with_one_and_no_search_both_for_the_research_task(self):
        env = Env(grounded(), {"terms": []})
        research_terms(env.search, env.read, items("Fern"), lang="vi", title="Frieren")
        (search_request,), (read_request,) = env.search.calls, env.read.calls
        self.assertEqual((search_request.task, search_request.web_search, search_request.response_schema), ("research", True, None))
        self.assertEqual((read_request.task, read_request.web_search), ("research", False))
        self.assertIsNotNone(read_request.response_schema)
        self.assertIn("Fern", search_request.user)
        self.assertIn("Frieren", search_request.user)
        # the reader sees the answer with its marks and the list of pages by site, not their links
        self.assertIn("the answer [0]", read_request.user)
        self.assertIn("[1] fandom.com", read_request.user)
        self.assertNotIn(REDIRECT, read_request.user)
        self.assertEqual((search_request.meta["kind"], read_request.meta["kind"]), ("research", "research"))

    def test_a_link_the_model_writes_is_never_a_source(self):
        reply = {"terms": [{"i": 0, "target": "Dũng sĩ Himmel", "source_refs": [0], "url": "https://evil.example/x", "note": "see https://evil.example/y"}]}
        env = Env(grounded("see https://evil.example/z for Himmel"), reply)
        outcome = research_terms(env.search, env.read, items("Himmel the Hero"), lang="vi")
        self.assertEqual([s.domain for s in outcome.drafts[0].sources], ["wikipedia.org"])
        self.assertNotIn("evil", json.dumps([[s.url for s in d.sources] for d in outcome.drafts]))

    def test_an_entry_with_no_real_page_or_no_form_proposes_nothing(self):
        reply = {"terms": [
            {"i": 0, "target": "A", "source_refs": []},  # no page
            {"i": 1, "target": "B", "source_refs": [9]},  # a page that is not in the list
            {"i": 2, "target": "", "source_refs": [0]},  # no form
            {"i": 7, "target": "C", "source_refs": [0]},  # an item that was not asked
            {"i": 3, "target": "Đê", "source_refs": [0, True, "1", 0]},  # junk refs are dropped, a repeat counts once
            "not an object",
        ]}
        env = Env(grounded(), reply)
        outcome = research_terms(env.search, env.read, items("a", "b", "c", "d"), lang="vi")
        self.assertEqual([d.payload["source"] for d in outcome.drafts], ["d"])
        self.assertEqual([len(d.sources) for d in outcome.drafts], [1])
        self.assertEqual([i.source for i in outcome.unanswered], ["a", "b", "c"])

    def test_a_form_equal_to_the_source_is_not_proposed_but_the_term_is_not_asked_again_at_once(self):
        env = Env(grounded(), {"terms": [{"i": 0, "target": "fern", "source_refs": [0]}]})
        outcome = research_terms(env.search, env.read, items("Fern"), lang="vi")
        self.assertEqual((outcome.drafts, [i.source for i in outcome.unanswered]), ((), ["Fern"]))

    def test_an_answer_with_no_page_behind_it_ends_after_one_call_with_everything_unanswered(self):
        env = Env(LLMResponse("I think it is Dũng sĩ Himmel"), {"terms": []})
        outcome = research_terms(env.search, env.read, items("Himmel"), lang="vi")
        self.assertEqual((outcome.calls, outcome.drafts, len(outcome.unanswered)), (1, (), 1))
        self.assertEqual(env.read.calls, [])  # no second call is bought for prose nobody can check

    def test_nothing_to_ask_costs_nothing_and_a_provider_without_search_is_not_asked(self):
        env = Env(grounded(), {"terms": []})
        self.assertEqual(research_terms(env.search, env.read, [], lang="vi").calls, 0)
        self.assertEqual(env.search.calls, [])
        blind = Env(grounded(), {"terms": []}, web_search=False)
        with self.assertRaises(ResearchError):
            research_terms(blind.search, blind.read, items("Fern"), lang="vi")
        self.assertEqual(blind.search.calls, [])

    def test_only_the_first_terms_up_to_the_limit_are_asked(self):
        env = Env(grounded(), {"terms": []})
        research_terms(env.search, env.read, items(*[f"T{n}" for n in range(MAX_TERMS + 5)]), lang="vi")
        self.assertIn(f"{MAX_TERMS - 1}. T{MAX_TERMS - 1}", env.search.calls[0].user)
        self.assertNotIn(f"T{MAX_TERMS}", env.search.calls[0].user)

    def test_an_empty_search_is_a_research_error_and_a_garbled_reading_is_tried_twice(self):
        def empty(req, n):
            raise BadOutput("empty response")

        search = FakeProvider(empty, caps=Capabilities(web_search=True))
        with self.assertRaises(ResearchError):
            research_terms(search, FakeProvider(), items("Fern"), lang="vi")
        garbled = Env(grounded(), "this is not json at all")
        with self.assertRaises(ResearchError):
            research_terms(garbled.search, garbled.read, items("Fern"), lang="vi")
        self.assertEqual(len(garbled.read.calls), 2)

    def test_a_provider_without_schema_support_is_asked_for_json_instead(self):
        env = Env(grounded(), {"terms": []})
        env.read.caps = Capabilities(structured_output=False, web_search=False)
        research_terms(env.search, env.read, items("Fern"), lang="vi")
        request = env.read.calls[0]
        self.assertEqual((request.response_schema, request.json_output), (None, True))


if __name__ == "__main__":
    unittest.main()
