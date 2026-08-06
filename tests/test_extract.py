"""Unit tests for extraction: retry, pagination, the silent clamp, pacing.

ARCHITECTURE §12 names these as worth testing because they are *branching*
logic — the kind that looks right, works on the happy path, and is wrong in a
way nothing surfaces until a backfill quietly returns half the data.

No network. Every test drives a fake session, so a failure means the logic is
wrong rather than that the API was slow.
"""

from __future__ import annotations

import json

import pytest
import requests

from ingestion.config import Settings
from ingestion.extract import ExtractError, Extractor, RateLimiter


def make_settings(**overrides) -> Settings:
    base = dict(
        source_base_url="https://example.test/f1",
        requests_per_hour=500, burst_per_second=1000,  # effectively no sleeping
        warehouse_host="h", warehouse_port="5432", warehouse_database="d",
        warehouse_user="u", warehouse_password="p",
        schema_raw="raw", schema_staging="staging", schema_marts="marts",
        lake_bucket="b", lake_region="r", lake_prefix="raw",
        aws_access_key_id="k", aws_secret_access_key="s", target_env="test",
    )
    return Settings(**(base | overrides))


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeSession:
    """Returns queued responses and records the params it was called with."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def mrdata(total, limit, offset, races=None):
    return {"MRData": {"total": str(total), "limit": str(limit),
                       "offset": str(offset),
                       "RaceTable": {"Races": races or []}}}


# --- the silent clamp ------------------------------------------------------

def test_offset_advances_by_reported_limit_not_requested():
    """The server clamps `limit` to 100 and still returns 200.

    A pager that advanced by what it *asked for* would skip rows and finish
    early while reporting success — the failure Phase 0 caught.
    """
    session = FakeSession([
        FakeResponse(body=mrdata(total=250, limit=100, offset=0)),
        FakeResponse(body=mrdata(total=250, limit=100, offset=100)),
        FakeResponse(body=mrdata(total=250, limit=100, offset=200)),
    ])
    extractor = Extractor(make_settings(), session=session)

    pages = list(extractor.paginate("2024/results"))

    assert [p.offset for p in pages] == [0, 100, 200]
    assert [call["offset"] for call in session.calls] == [0, 100, 200]
    assert pages[-1].is_last


def test_pagination_stops_on_reported_total_not_empty_page():
    session = FakeSession([
        FakeResponse(body=mrdata(total=150, limit=100, offset=0)),
        FakeResponse(body=mrdata(total=150, limit=100, offset=100)),
    ])
    extractor = Extractor(make_settings(), session=session)

    pages = list(extractor.paginate("2024/results"))

    assert len(pages) == 2
    assert len(session.calls) == 2, "must not request a page beyond the total"


def test_zero_limit_raises_instead_of_looping_forever():
    """A limit of 0 would advance the offset by nothing — an infinite loop."""
    session = FakeSession([FakeResponse(body=mrdata(total=100, limit=0, offset=0))])
    extractor = Extractor(make_settings(), session=session)

    with pytest.raises(ExtractError, match="cannot paginate"):
        list(extractor.paginate("2024/results"))


# --- retry -----------------------------------------------------------------

def test_retries_transient_status_then_succeeds():
    session = FakeSession([
        FakeResponse(status_code=503),
        FakeResponse(status_code=500),
        FakeResponse(body=mrdata(total=1, limit=100, offset=0)),
    ])
    extractor = Extractor(make_settings(), session=session)
    extractor.session = session

    page = extractor.fetch_page("2024/results")

    assert page.total == 1
    assert len(session.calls) == 3


def test_does_not_retry_non_transient_status():
    """A 404 is an answer. Retrying it burns rate budget three more times."""
    session = FakeSession([FakeResponse(status_code=404, body={})])
    extractor = Extractor(make_settings(), session=session)

    with pytest.raises(ExtractError, match="not retryable"):
        extractor.fetch_page("2024/nope")

    assert len(session.calls) == 1


def test_gives_up_after_max_attempts():
    session = FakeSession([FakeResponse(status_code=503) for _ in range(4)])
    extractor = Extractor(make_settings(), session=session)

    with pytest.raises(ExtractError):
        extractor.fetch_page("2024/results")

    assert len(session.calls) == 4, "should stop at MAX_ATTEMPTS, not retry forever"


def test_retries_connection_errors():
    session = FakeSession([
        requests.ConnectionError("reset"),
        FakeResponse(body=mrdata(total=1, limit=100, offset=0)),
    ])
    extractor = Extractor(make_settings(), session=session)

    page = extractor.fetch_page("2024/results")

    assert page.total == 1


def test_honours_retry_after_header(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("ingestion.extract.time.sleep", lambda s: slept.append(s))

    session = FakeSession([
        FakeResponse(status_code=429, headers={"Retry-After": "7"}),
        FakeResponse(body=mrdata(total=1, limit=100, offset=0)),
    ])
    Extractor(make_settings(), session=session).fetch_page("2024/results")

    assert 7 in slept, "must wait the interval the server asked for, not a guess"


# --- string-typed metadata -------------------------------------------------

def test_parses_string_pagination_metadata():
    """Every scalar from this source is a string, including total/limit/offset."""
    session = FakeSession([FakeResponse(body=mrdata(total="479", limit="100", offset="0"))])
    page = Extractor(make_settings(), session=session).fetch_page("2024/results")

    assert (page.total, page.limit, page.offset) == (479, 100, 0)
    assert isinstance(page.total, int)


def test_missing_metadata_falls_back_without_crashing():
    session = FakeSession([FakeResponse(body={"MRData": {}})])
    page = Extractor(make_settings(), session=session).fetch_page("2024/results", offset=5)

    assert page.offset == 5
    assert page.total == 0


# --- rate limiting ---------------------------------------------------------

def test_rate_limiter_waits_when_hourly_budget_is_spent(monkeypatch):
    """The hourly budget binds before the burst limit does."""
    clock = {"now": 1000.0}
    slept: list[float] = []
    monkeypatch.setattr("ingestion.extract.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("ingestion.extract.time.sleep",
                        lambda s: (slept.append(s), clock.__setitem__("now", clock["now"] + s)))

    limiter = RateLimiter(requests_per_hour=3, burst_per_second=1000)
    for _ in range(3):
        limiter.acquire()
        clock["now"] += 1

    assert not slept, "should not wait while budget remains"

    limiter.acquire()
    assert slept, "must wait once the hourly budget is exhausted"
    assert slept[0] > 3500, "should wait for the oldest call to leave the window"


def test_rate_limiter_enforces_burst_interval(monkeypatch):
    clock = {"now": 500.0}
    slept: list[float] = []
    monkeypatch.setattr("ingestion.extract.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("ingestion.extract.time.sleep",
                        lambda s: (slept.append(s), clock.__setitem__("now", clock["now"] + s)))

    limiter = RateLimiter(requests_per_hour=500, burst_per_second=4)
    limiter.acquire()
    limiter.acquire()

    assert slept and slept[0] == pytest.approx(0.25, abs=0.01)


def test_window_slides_rather_than_resetting(monkeypatch):
    """A fixed window allows a double burst across the boundary."""
    clock = {"now": 0.0}
    monkeypatch.setattr("ingestion.extract.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("ingestion.extract.time.sleep", lambda s: None)

    limiter = RateLimiter(requests_per_hour=2, burst_per_second=1000)
    limiter.acquire()
    clock["now"] += 3601  # first call ages out
    limiter.acquire()

    assert limiter.used_in_window == 1
