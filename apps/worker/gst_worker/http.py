from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        return


class JsonFileCache(MemoryCache):
    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                logging.warning("Ignoring unreadable cache file: %s", self.path)
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class HTTPClient:
    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        return self.request_json("GET", url, params=params, headers=headers)

    def request_json(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=headers or {}, method=method)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read()
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))


def cached_get_json(
    cache: MemoryCache,
    client: HTTPClient,
    url: str,
    params: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Any:
    key = json.dumps([url, sorted(params.items())], ensure_ascii=True)
    cached = cache.get(key)
    if cached is not None:
        return cached
    value = client.get_json(url, params=params, headers=headers)
    cache.set(key, value)
    cache.save()
    return value


def first_data(response: Any) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list) and data:
        return data[0]
    return {}


class HTTPTransport:
    """Small transport interface consumed by integration adapters."""
    def __init__(self, client: HTTPClient) -> None:
        self.client = client
    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.client.get_json(url, **kwargs)
    def request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        return self.client.request_json(method, url, **kwargs)
