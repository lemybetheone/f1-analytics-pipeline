"""Unit tests for the rate budget surviving a dropped connection.

The budget connection is the longest-lived thing in the process — a backfill
holds it for hours while it sleeps out the rate limit — which makes it exactly
what a connection pooler reaps. The 2026-09-12 backfill lost it after 6.5 hours
and took that season's whole phase down with it (ARCHITECTURE decision 32).

No database. Every test drives a fake connection, so a failure means the retry
logic is wrong rather than that the warehouse was unreachable.
"""

from __future__ import annotations

import psycopg
import pytest

from ingestion.load_warehouse import WarehouseBudget


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=None):
        self._conn.executed += 1
        if self._conn.dead:
            raise psycopg.OperationalError("server closed the connection unexpectedly")

    def fetchone(self):
        return (7, 42.0)

    @property
    def rowcount(self):
        return 1


class FakeConnection:
    def __init__(self, dead: bool = False):
        self.dead = dead
        self.executed = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("ingestion.load_warehouse.time.sleep", lambda _: None)


def make_budget(monkeypatch, connections):
    """A budget whose _connect hands out the given connections in order."""
    handed: list[FakeConnection] = []

    def fake_connect(self):
        conn = connections[len(handed)]
        handed.append(conn)
        return conn

    monkeypatch.setattr(WarehouseBudget, "_connect", fake_connect)

    class Stub:
        schema_raw = "raw"

    budget = WarehouseBudget(Stub())
    return budget, handed


def test_record_reconnects_after_the_server_drops_the_link(monkeypatch):
    """The 1961 failure: a reaped connection must not end the run."""
    dead, alive = FakeConnection(dead=True), FakeConnection()
    budget, handed = make_budget(monkeypatch, [dead, alive])

    budget.record("results")  # would have raised before the fix

    assert dead.closed, "the dead connection is closed rather than leaked"
    assert len(handed) == 2, "exactly one reconnect"
    assert alive.executed == 1, "the statement ran on the new connection"


def test_usage_reconnects_and_still_returns_the_reading(monkeypatch):
    """A dropped link must not be reported as an empty budget.

    Returning (0, 0.0) here would read as 'no calls used' and let the process
    spend the whole allowance again — the failure mode the shared budget exists
    to prevent.
    """
    budget, _ = make_budget(monkeypatch, [FakeConnection(dead=True), FakeConnection()])

    assert budget.usage(3600) == (7, 42.0)


def test_a_genuinely_unreachable_database_still_raises(monkeypatch):
    """Retrying must not become swallowing.

    A budget that cannot be recorded has to fail loudly: the alternative is a
    process spending the allowance blind, which is what the terms let the
    source block us for.
    """
    budget, handed = make_budget(
        monkeypatch, [FakeConnection(dead=True) for _ in range(WarehouseBudget.MAX_ATTEMPTS)])

    with pytest.raises(psycopg.OperationalError):
        budget.record("results")

    assert len(handed) == WarehouseBudget.MAX_ATTEMPTS, "bounded, not infinite"
