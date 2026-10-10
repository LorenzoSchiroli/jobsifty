"""Web search providers behind one interface, tried in order as a pool.

Free search allowances come and go, so no single provider is relied on. Each
adapter maps its API onto `SearchResult` (hits + an optional answer) and its
error statuses onto three outcomes:

- `RateLimited`: the provider is paused (Retry-After, else a minute) and the
  query goes to the next one;
- `Unusable`: out of credits or a bad key; skipped for the rest of the run;
- `Transient`: network error or 5xx; the query goes to the next one, and a
  provider failing several times in a row is paused too.

The pool never waits for a paused provider: when none is left, the query
returns None and the company is enriched without that search.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 30
_DEFAULT_PAUSE = 60.0
_TRANSIENT_PAUSE_AFTER = 3
_TRANSIENT_PAUSE = 300.0
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


class ProviderError(Exception):
    pass


class RateLimited(ProviderError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Unusable(ProviderError):
    pass


class Transient(ProviderError):
    pass


def _retry_after(resp: requests.Response) -> float | None:
    try:
        return float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None


class Provider:
    """One search API. Subclasses set the class attributes and build/parse requests."""

    name: str
    env_key: str
    # client-side spacing between calls, to stay under the provider's per-minute limit
    min_interval: float = 0.0
    # HTTP statuses meaning no later call this run can succeed (bad key, no credits)
    unusable_statuses: frozenset[int] = frozenset({401, 402, 403})

    def __init__(self, api_key: str | None):
        self.api_key = api_key
        self.disabled = not api_key
        self.paused_until = 0.0
        self.calls = self.ok = self.failed = 0
        self._consecutive_failures = 0
        self._last_call = 0.0
        self._lock = threading.Lock()

    def usable(self) -> bool:
        return not self.disabled and time.monotonic() >= self.paused_until

    def search(self, query: str, max_results: int, include_domains: list[str] | None = None,
               answer: bool = False) -> SearchResult:
        self._wait_for_slot()
        self.calls += 1
        try:
            resp = self._send(query, max_results, include_domains, answer)
        except requests.RequestException as e:
            raise Transient(str(e)) from e
        if resp.status_code == 200:
            return SearchResult(self.name, *self._parse(resp.json(), max_results))
        detail = f"HTTP {resp.status_code} {resp.text[:200]}"
        if resp.status_code == 429:
            raise RateLimited(detail, _retry_after(resp))
        if resp.status_code in self.unusable_statuses:
            raise Unusable(detail)
        raise Transient(detail)

    def _wait_for_slot(self) -> None:
        with self._lock:
            if (wait := self._last_call + self.min_interval - time.monotonic()) > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _send(self, query, max_results, include_domains, answer) -> requests.Response:
        raise NotImplementedError

    def _parse(self, body: dict, max_results: int) -> tuple[list[Hit], str | None]:
        raise NotImplementedError

    # outcome bookkeeping, called by the pool
    def succeeded(self) -> None:
        self.ok += 1
        self._consecutive_failures = 0

    def rate_limited(self, retry_after: float | None) -> None:
        self.failed += 1
        self.paused_until = time.monotonic() + (retry_after or _DEFAULT_PAUSE)

    def unusable(self) -> None:
        self.failed += 1
        self.disabled = True

    def transient(self) -> None:
        self.failed += 1
        self._consecutive_failures += 1
        if self._consecutive_failures >= _TRANSIENT_PAUSE_AFTER:
            self._consecutive_failures = 0
            self.paused_until = time.monotonic() + _TRANSIENT_PAUSE


class TinyFish(Provider):
    """Free, rate limited (30/minute, 500/hour); 402 = daily allowance used up."""

    name = "tinyfish"
    env_key = "TINYFISH_API_KEY"
    min_interval = 2.1

    def _send(self, query, max_results, include_domains, answer):
        params = {"query": query}
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        return requests.get("https://api.search.tinyfish.ai", params=params,
                            headers={"X-API-Key": self.api_key}, timeout=_TIMEOUT)

    def _parse(self, body, max_results):
        hits = [Hit(h["url"], h.get("snippet") or h.get("title") or "") for h in body.get("results", []) if h.get("url")]
        return hits[:max_results], None


class Exa(Provider):
    """Credits ($10/month free); 402 = out of credits."""

    name = "exa"
    env_key = "EXA_API_KEY"

    def _send(self, query, max_results, include_domains, answer):
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


class Tavily(Provider):
    """1,000 credits/month free; 432/433 = plan or pay-as-you-go limit. The only one with a short answer."""

    name = "tavily"
    env_key = "TAVILY_API_KEY"
    unusable_statuses = frozenset({401, 402, 403, 432, 433})

    def _send(self, query, max_results, include_domains, answer):
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


PROVIDERS: dict[str, type[Provider]] = {p.name: p for p in (TinyFish, Exa, Tavily)}


class SearchPool:
    """Providers in priority order; each query goes to the first usable one."""

    def __init__(self, providers: list[Provider]):
        self.providers = providers

    @classmethod
    def from_env(cls) -> "SearchPool":
        """`SEARCH_PROVIDERS` (comma-separated) sets the order; providers without a key are skipped."""
        names = [n.strip().lower() for n in os.environ.get("SEARCH_PROVIDERS", "").split(",") if n.strip()]
        providers = []
        for name in names or DEFAULT_ORDER:
            if name not in PROVIDERS:
                logger.warning("Unknown search provider %r ignored", name)
                continue
            provider_cls = PROVIDERS[name]
            providers.append(provider_cls(os.environ.get(provider_cls.env_key)))
        return cls(providers)

    def configured(self) -> list[str]:
        return [p.name for p in self.providers if p.api_key]

    def down(self) -> bool:
        """True when providers were configured but all are out for the rest of the run."""
        return bool(self.configured()) and all(p.disabled for p in self.providers)

    def search(self, query: str, max_results: int = 5, include_domains: list[str] | None = None,
               answer: bool = False) -> SearchResult | None:
        for provider in self.providers:
            if not provider.usable():
                continue
            try:
                result = provider.search(query, max_results, include_domains, answer)
            except RateLimited as e:
                provider.rate_limited(e.retry_after)
                logger.warning("%s rate limited (%s); paused", provider.name, e)
            except Unusable as e:
                provider.unusable()
                logger.warning("%s unusable for the rest of the run (%s)", provider.name, e)
            except Transient as e:
                provider.transient()
                logger.warning("%s search failed for %r: %s", provider.name, query, e)
            except Exception as e:  # unexpected response shape
                provider.transient()
                logger.warning("%s returned an unusable response for %r: %s", provider.name, query, e)
            else:
                provider.succeeded()
                return result
        logger.warning("No search provider available for %r", query)
        return None

    def calls(self) -> int:
        return sum(p.calls for p in self.providers)

    def stats(self) -> str:
        """'tinyfish 12 ok/1 failed, exa 1 ok, tavily no key'"""
        parts = []
        for p in self.providers:
            if not p.api_key:
                parts.append(f"{p.name} no key")
                continue
            part = f"{p.name} {p.ok} ok" + (f"/{p.failed} failed" if p.failed else "")
            parts.append(part + (" (disabled)" if p.disabled else ""))
        return ", ".join(parts) or "none"
