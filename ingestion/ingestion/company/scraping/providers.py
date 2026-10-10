"""Web search providers behind one interface, tried in order as a pool (`ingestion/pool.py`).

Each adapter maps its API onto `SearchResult` (hits + an optional answer); the
pool handles rate limits, exhausted credits and failures.
"""
import os
from dataclasses import dataclass, field

import requests

from ingestion.pool import Pool, Provider, order_from_env

_TIMEOUT = 30
DEFAULT_ORDER = ("tinyfish", "exa", "tavily")


@dataclass
class Hit:
    url: str
    text: str = ""


@dataclass
class SearchResult:
    provider: str
    hits: list[Hit] = field(default_factory=list)
    answer: str | None = None


class SearchProvider(Provider):
    def search(self, query: str, max_results: int, include_domains: list[str] | None = None,
               answer: bool = False) -> SearchResult:
        resp = self._send(lambda: self._request(query, max_results, include_domains, answer))
        return SearchResult(self.name, *self._parse(resp.json(), max_results))

    def _request(self, query, max_results, include_domains, answer) -> requests.Response:
        raise NotImplementedError

    def _parse(self, body: dict, max_results: int) -> tuple[list[Hit], str | None]:
        raise NotImplementedError


class TinyFish(SearchProvider):
    """Free, rate limited (30/minute, 500/hour); 402 = daily allowance used up."""

    name = "tinyfish"
    env_key = "TINYFISH_API_KEY"
    min_interval = 2.1

    def _request(self, query, max_results, include_domains, answer):
        params = {"query": query}
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        return requests.get("https://api.search.tinyfish.ai", params=params,
                            headers={"X-API-Key": self.api_key}, timeout=_TIMEOUT)

    def _parse(self, body, max_results):
        hits = [Hit(h["url"], h.get("snippet") or h.get("title") or "") for h in body.get("results", []) if h.get("url")]
        return hits[:max_results], None


class Exa(SearchProvider):
    """Credits ($10/month free); 402 = out of credits."""

    name = "exa"
    env_key = "EXA_API_KEY"

    def _request(self, query, max_results, include_domains, answer):
        body = {"query": query, "numResults": max_results, "type": "auto",
                "contents": {"highlights": {"maxCharacters": 400}}}
        if include_domains:
            body["includeDomains"] = include_domains
        return requests.post("https://api.exa.ai/search", json=body,
                             headers={"x-api-key": self.api_key}, timeout=_TIMEOUT)

    def _parse(self, body, max_results):
        hits = [Hit(h["url"], " ".join(h.get("highlights") or []) or h.get("title") or "")
                for h in body.get("results", []) if h.get("url")]
        return hits, None


class Tavily(SearchProvider):
    """1,000 credits/month free; 432/433 = plan or pay-as-you-go limit. The only one with a short answer."""

    name = "tavily"
    env_key = "TAVILY_API_KEY"
    unusable_statuses = frozenset({401, 402, 403, 432, 433})

    def _request(self, query, max_results, include_domains, answer):
        body = {"query": query, "search_depth": "basic", "max_results": max_results}
        if include_domains:
            body["include_domains"] = include_domains
        if answer:
            body["include_answer"] = "basic"
        return requests.post("https://api.tavily.com/search", json=body,
                             headers={"Authorization": f"Bearer {self.api_key}"}, timeout=_TIMEOUT)

    def _parse(self, body, max_results):
        hits = [Hit(h["url"], h.get("content") or "") for h in body.get("results", []) if h.get("url")]
        return hits, body.get("answer") or None


PROVIDERS: dict[str, type[SearchProvider]] = {p.name: p for p in (TinyFish, Exa, Tavily)}


class SearchPool(Pool[SearchProvider]):
    @classmethod
    def from_env(cls) -> "SearchPool":
        """`SEARCH_PROVIDERS` (comma-separated) sets the order; providers without a key are skipped."""
        names = order_from_env(os.environ.get("SEARCH_PROVIDERS"), DEFAULT_ORDER, PROVIDERS)
        return cls([PROVIDERS[n](os.environ.get(PROVIDERS[n].env_key)) for n in names])

    def search(self, query: str, max_results: int = 5, include_domains: list[str] | None = None,
               answer: bool = False) -> SearchResult | None:
        return self.call(repr(query), lambda p: p.search(query, max_results, include_domains, answer))
