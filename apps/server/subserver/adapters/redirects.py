"""Turn a grounding link into the page it stands for (P16, FR-7).

A grounded Gemini answer names its pages by a link of Google's own (``vertexaisearch.cloud.google.com/grounding-api-redirect/...``)
that answers ``302`` with the page's address and is not a lasting link. Review shows the sites a research proposal rests on
and "two independent sources" is counted by site, so the worker follows that one hop once, when the research runs, and keeps
the page's own address. Only Google's redirect host is ever fetched (the answer of a model decides which path, never which
host), nothing is followed past the first hop, and a failure is just "unknown": the caller falls back to the site's name.
"""

from __future__ import annotations

import http.client
import logging
from typing import Iterable
from urllib.parse import urlsplit

GOOGLE_REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)
MAX_URL = 2048
TIMEOUT_SECONDS = 6.0


class RedirectResolver:
    """``resolve(url)`` -> the address of the page ``url`` stands for, or ``None``: a link of Google's redirect host is followed one
    hop (``None`` on an error, a timeout or a destination that is not an ``http(s)`` address); any other ``http(s)`` address is
    already a page and comes back as it is, never fetched. One request per link at most, never raises."""

    def __init__(
        self, *, hosts: Iterable[str] = GOOGLE_REDIRECT_HOSTS, schemes: Iterable[str] = ("https",), timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.hosts = frozenset(h.lower() for h in hosts)
        self.schemes = frozenset(schemes)
        self.timeout = timeout

    def resolve(self, url: str) -> str | None:
        try:
            parts = urlsplit(url or "")
            host = (parts.hostname or "").lower()
            if host not in self.hosts:
                return _page(url)  # not Google's: a provider that gives a real address (not a redirect) needs no hop
            if parts.scheme not in self.schemes:
                return None
            connection_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            connection = connection_class(host, parts.port, timeout=self.timeout)
            try:
                target = parts.path or "/"
                if parts.query:
                    target += "?" + parts.query
                connection.request("GET", target, headers={"User-Agent": "llm-subtitle-translator", "Accept": "*/*"})
                response = connection.getresponse()
                where = response.getheader("Location") if 300 <= response.status < 400 else None
            finally:
                connection.close()
            return _page(where)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            logging.info("Could not follow a grounding link (%s)", type(exc).__name__)
            return None


def _page(location: str | None) -> str | None:
    """``location`` when it is an absolute ``http(s)`` address of a site; anything else is not a page."""
    text = (location or "").strip()
    if not text or len(text) > MAX_URL:
        return None
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return text
