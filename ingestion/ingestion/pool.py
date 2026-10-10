"""Free-tier API providers tried in order, as a pool.

Free allowances come and go, so no single provider is relied on. Every HTTP
failure maps onto one of three outcomes:

- `RateLimited`: the provider is paused (Retry-After, else a minute) and the
  call goes to the next one;
- `Unusable`: out of credits, daily quota used up, bad key or unknown model;
  skipped for the rest of the run;
- `Transient`: network error, timeout, 5xx or an unusable answer; the call goes
  to the next one, and a provider failing several times in a row is paused too.

The pool never waits for a paused provider: when none is left, the call
returns None and the caller carries on without it.
"""
import logging
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

import requests

logger = logging.getLogger(__name__)

_DEFAULT_PAUSE = 60.0
_TRANSIENT_PAUSE_AFTER = 3
_TRANSIENT_PAUSE = 300.0
_DAILY_QUOTA_MARKERS = ("per day", "perday", "(tpd)", "(rpd)", "daily")


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
    """One API with its own key, pacing and run state. Subclasses set the class attributes."""

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

    def _wait_for_slot(self) -> None:
        with self._lock:
            if (wait := self._last_call + self.min_interval - time.monotonic()) > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            self.calls += 1

    def _send(self, send: Callable[[], requests.Response]) -> requests.Response:
        """Paced request; raises the outcome of anything but a 200."""
        self._wait_for_slot()
        try:
            resp = send()
        except requests.RequestException as e:
            raise Transient(str(e)) from e
        if resp.status_code == 200:
            return resp
        detail = f"HTTP {resp.status_code} {resp.text[:200]}"
        if resp.status_code == 429:
            # a daily quota will not come back within this run
            if any(marker in resp.text.lower() for marker in _DAILY_QUOTA_MARKERS):
                raise Unusable(detail)
            raise RateLimited(detail, _retry_after(resp))
        if resp.status_code in self.unusable_statuses:
            raise Unusable(detail)
        raise Transient(detail)

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


P = TypeVar("P", bound=Provider)
T = TypeVar("T")


class Pool(Generic[P]):
    """Providers in priority order; each call goes to the first usable one."""

    def __init__(self, providers: list[P]):
        self.providers = providers

    def configured(self) -> list[str]:
        return [p.name for p in self.providers if p.api_key]

    def down(self) -> bool:
        """True when providers were configured but all are out for the rest of the run."""
        return bool(self.configured()) and all(p.disabled for p in self.providers)

    def call(self, what: str, fn: Callable[[P], T]) -> T | None:
        """`fn(provider)` on the first usable provider that succeeds; None when none does."""
        for provider in self.providers:
            if not provider.usable():
                continue
            try:
                result = fn(provider)
            except RateLimited as e:
                provider.rate_limited(e.retry_after)
                logger.warning("%s rate limited (%s); paused", provider.name, e)
            except Unusable as e:
                provider.unusable()
                logger.warning("%s unusable for the rest of the run (%s)", provider.name, e)
            except Transient as e:
                provider.transient()
                logger.warning("%s failed for %s: %s", provider.name, what, e)
            except Exception as e:  # unexpected response shape
                provider.transient()
                logger.warning("%s returned an unusable response for %s: %s", provider.name, what, e)
            else:
                provider.succeeded()
                return result
        logger.warning("No provider available for %s", what)
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


def order_from_env(value: str | None, default: tuple[str, ...], known: dict) -> list[str]:
    """A comma-separated provider order, unknown names dropped with a warning."""
    names = [n.strip().lower() for n in (value or "").split(",") if n.strip()] or list(default)
    for name in names:
        if name not in known:
            logger.warning("Unknown provider %r ignored", name)
    return [n for n in names if n in known]
