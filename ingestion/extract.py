"""Extract from the source API: retry, pacing, pagination.

The only module that talks to the source. Everything downstream consumes the
bytes it returns.

Three Phase 0 findings are encoded here as behaviour rather than comments:

* **The page cap is 100 and the server clamps silently.** Ask for 2000 and it
  returns HTTP 200 with `"limit": "100"`. A pager that trusted its own request
  size would compute the wrong number of pages, stop short, and report success.
  So the *reported* limit is what advances the offset, never the requested one.

* **No rate-limit headers are returned.** Remaining allowance cannot be read at
  runtime, so it has to be counted locally against the published budget.

* **The hourly budget binds, not the burst.** 4 req/s is fine for a handful of
  calls; sustained, it would exhaust 500/hour in about two minutes.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from ingestion.config import Settings

USER_AGENT = "f1-analytics-pipeline (+https://github.com/lemybetheone/f1-analytics-pipeline)"

# ARCHITECTURE §5: transient failures are retried with exponential backoff;
# non-transient ones fail fast. A 404 is an answer, not an outage — retrying it
# just burns the rate budget three more times.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0

# The observed hard cap. Requesting more is silently clamped, so requesting
# more is pointless.
PAGE_SIZE = 100


class ExtractError(RuntimeError):
    """A request failed in a way retrying will not fix."""


@dataclass
class Page:
    """One page of results, with both the raw bytes and the parsed payload.

    The bytes are what get landed in the lake: raw means *what the source
    returned*, not a re-serialised copy that has been through a JSON round-trip
    and may differ in key order or float formatting.
    """

    url: str
    params: dict[str, Any]
    content: bytes
    payload: dict
    offset: int
    limit: int
    total: int

    @property
    def is_last(self) -> bool:
        return self.offset + self.limit >= self.total


class BudgetStore(Protocol):
    """Where spent API calls are recorded, so the budget can be counted.

    Deliberately narrow so `extract` stays free of any database dependency: the
    warehouse-backed implementation lives in `load_warehouse`, and this module
    never imports psycopg. Both implementations answer the same two questions,
    and neither exposes a clock — the limiter must not have to reconcile
    `time.monotonic()` with a database's `now()`.
    """

    def usage(self, window_seconds: int) -> tuple[int, float]:
        """(calls inside the window, seconds until the oldest leaves it)."""
        ...

    def record(self, entity: str | None = None) -> None:
        """Record that a call has just been spent."""
        ...


class InMemoryBudget:
    """Per-process budget. Correct for a single run, blind across processes."""

    def __init__(self) -> None:
        self._calls: deque[float] = deque()

    def _prune(self, window_seconds: int) -> None:
        cutoff = time.monotonic() - window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    def usage(self, window_seconds: int) -> tuple[int, float]:
        self._prune(window_seconds)
        if not self._calls:
            return (0, 0.0)
        seconds_until_free = window_seconds - (time.monotonic() - self._calls[0])
        return (len(self._calls), max(seconds_until_free, 0.0))

    def record(self, entity: str | None = None) -> None:
        self._calls.append(time.monotonic())


class RateLimiter:
    """Enforce both the burst and the sustained budget.

    The API publishes 4 requests/second and 500/hour and returns no headers, so
    compliance is entirely on the client. Exceeding it risks being blocked
    without notice, which the terms explicitly permit — and the source is the
    one dependency that cannot be rebuilt from the lake.

    A sliding window rather than a fixed one: a fixed hourly window lets you
    spend the whole budget in the last minute of one hour and the first minute
    of the next, which is 1,000 requests in two minutes and exactly the burst
    the limit exists to prevent.

    The **sustained** budget is delegated to a `BudgetStore` so it can be shared
    across processes and survive a restart. The **burst** limit stays in-process
    on purpose: it exists to avoid hammering the server within a second, a
    round-trip to a shared store per request would cost more than it protects,
    and the sustained budget is the one that actually binds.
    """

    def __init__(self, requests_per_hour: int, burst_per_second: int,
                 store: BudgetStore | None = None) -> None:
        self._window_seconds = 3600
        self._max_in_window = requests_per_hour
        self._min_interval = 1 / burst_per_second
        self._store: BudgetStore = store or InMemoryBudget()
        self._last_call = 0.0

    def acquire(self, entity: str | None = None) -> float:
        """Block until a request may be made. Returns seconds waited."""
        waited = 0.0

        gap = time.monotonic() - self._last_call
        if self._last_call and gap < self._min_interval:
            time.sleep(self._min_interval - gap)
            waited += self._min_interval - gap

        used, seconds_until_free = self._store.usage(self._window_seconds)
        if used >= self._max_in_window:
            # Wait for the oldest call to age out of the window. The small
            # margin avoids waking a hair early and immediately sleeping again.
            sleep_for = max(seconds_until_free, 0.0) + 0.01
            time.sleep(sleep_for)
            waited += sleep_for

        self._store.record(entity)
        self._last_call = time.monotonic()
        return waited

    @property
    def used_in_window(self) -> int:
        return self._store.usage(self._window_seconds)[0]


class Extractor:
    """Fetches pages from the source API."""

    def __init__(self, settings: Settings, session: requests.Session | None = None,
                 budget: BudgetStore | None = None, entity: str | None = None) -> None:
        self.settings = settings
        self.entity = entity
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.limiter = RateLimiter(settings.requests_per_hour,
                                   settings.burst_per_second, store=budget)

    def fetch_page(self, path: str, offset: int = 0, limit: int = PAGE_SIZE) -> Page:
        """Fetch one page, retrying only what is worth retrying."""
        url = f"{self.settings.source_base_url}/{path.strip('/')}.json"
        params = {"limit": limit, "offset": offset}
        last_error: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            # Every attempt counts against the budget, including retries — the
            # server charges for a request whether or not it answers usefully.
            self.limiter.acquire(self.entity)
            try:
                response = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt == MAX_ATTEMPTS:
                    break
                time.sleep(BACKOFF_BASE_SECONDS ** attempt)
                continue

            if response.status_code in RETRYABLE_STATUS:
                if attempt == MAX_ATTEMPTS:
                    last_error = f"HTTP {response.status_code} after {attempt} attempts"
                    break
                # Honour Retry-After when the server states it. Guessing when
                # you have been told is how a 429 becomes a block.
                wait = float(response.headers.get("Retry-After") or BACKOFF_BASE_SECONDS ** attempt)
                time.sleep(wait)
                continue

            if response.status_code != 200:
                raise ExtractError(
                    f"HTTP {response.status_code} for {url} "
                    f"(offset={offset}) — not retryable: {response.text[:200]}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise ExtractError(f"{url} returned non-JSON: {exc}") from exc

            meta = payload.get("MRData", {})
            return Page(
                url=url,
                params=params,
                content=response.content,
                payload=payload,
                offset=_as_int(meta.get("offset"), offset),
                # The *reported* limit, not the requested one — the server
                # clamps silently and only this value reflects reality.
                limit=_as_int(meta.get("limit"), limit),
                total=_as_int(meta.get("total"), 0),
            )

        raise ExtractError(f"{url} (offset={offset}) failed: {last_error}")

    def paginate(self, path: str) -> Iterator[Page]:
        """Yield every page for a path, stopping when the reported total is reached.

        Termination is driven by the total the server reports, not by an empty
        page. An endpoint that returns a short page mid-run would otherwise end
        the loop early and look like a clean finish.
        """
        offset = 0
        while True:
            page = self.fetch_page(path, offset=offset)
            yield page

            if page.limit <= 0:  # would loop forever
                raise ExtractError(f"{path} reported limit={page.limit}; cannot paginate")
            if page.is_last:
                return
            offset += page.limit


def _as_int(value: Any, default: int) -> int:
    """Every scalar from this source is a string, including pagination metadata."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
