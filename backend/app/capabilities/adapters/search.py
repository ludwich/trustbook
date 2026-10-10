"""Web search adapters: SearXNG (bundled), Brave and Tavily."""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

from app.capabilities.adapters.base import AdapterError, SearchResult, parse_json, send
from app.capabilities.http import OutboundHttp

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"
_TAG_RE = re.compile(r"<[^>]+>")


def clean_snippet(text: Any) -> str:
    """Strip HTML tags and entities from a result snippet."""
    return " ".join(html.unescape(_TAG_RE.sub("", str(text or ""))).split())


def _results(items: Any, snippet_key: str, limit: int) -> list[SearchResult]:
    if not isinstance(items, list):
        raise AdapterError("bad_response", "The search service did not return a result list.")
    results: list[SearchResult] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        results.append(
            SearchResult(
                title=clean_snippet(item.get("title")) or str(item["url"]),
                url=str(item["url"]),
                snippet=clean_snippet(item.get(snippet_key)),
            )
        )
        if len(results) >= limit:
            break
    return results


class SearxngAdapter:
    """``GET {base}/search?q=…&format=json`` on the bundled SearXNG container."""

    def __init__(self, http: OutboundHttp, *, base_url: str, timeout: float = 20.0) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        request = httpx.Request(
            "GET",
            f"{self._base_url}/search",
            params={"q": query, "format": "json"},
            headers={"Accept": "application/json"},
        )
        with self._http.client("web_search", timeout=self._timeout) as client:
            try:
                response = send(client, request)
            except AdapterError as exc:
                if exc.kind == "auth":
                    raise AdapterError(
                        "bad_request",
                        "SearXNG refused JSON results. Its settings must allow the json format.",
                        status=exc.status,
                    ) from exc
                raise
        data = parse_json(response, what="search service")
        return _results(data.get("results") if isinstance(data, dict) else None, "content", limit)


class BraveAdapter:
    """Brave Search API (key in the ``X-Subscription-Token`` header)."""

    def __init__(self, http: OutboundHttp, *, api_key: str, timeout: float = 20.0) -> None:
        self._http = http
        self._api_key = api_key
        self._timeout = timeout

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        request = httpx.Request(
            "GET",
            BRAVE_URL,
            params={"q": query, "count": max(1, min(limit, 20))},
            headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
        )
        with self._http.client("web_search", timeout=self._timeout) as client:
            response = send(client, request, secret=self._api_key)
        data = parse_json(response, what="search service")
        web = data.get("web") if isinstance(data, dict) else None
        items = web.get("results", []) if isinstance(web, dict) else []
        return _results(items, "description", limit)


class TavilyAdapter:
    """Tavily search API (key in the ``Authorization: Bearer`` header, never in the body)."""

    def __init__(self, http: OutboundHttp, *, api_key: str, timeout: float = 30.0) -> None:
        self._http = http
        self._api_key = api_key
        self._timeout = timeout

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        request = httpx.Request(
            "POST",
            TAVILY_URL,
            json={"query": query, "max_results": max(1, min(limit, 20)), "search_depth": "basic"},
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        with self._http.client("web_search", timeout=self._timeout) as client:
            response = send(client, request, secret=self._api_key)
        data = parse_json(response, what="search service")
        return _results(data.get("results") if isinstance(data, dict) else None, "content", limit)
