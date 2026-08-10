"""Load the warehouse **from the lake**, plus dead-letter and checkpoint state.

ARCHITECTURE decision 2: the warehouse reads lake objects, never the API
response still held in memory. That is what makes this step independently
replayable — and it is a rule the code has to actually follow. An in-memory
shortcut would work fine today and quietly make the architecture diagram false.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ingestion.config import Settings
from ingestion.entities import EntitySpec, Record


@dataclass
class LoadOutcome:
    parsed: int = 0
    inserted: int = 0
    updated: int = 0
    failed: int = 0


class Warehouse:
    """Owns the connection and every statement issued against `raw`."""

    def __init__(self, settings: Settings, connection=None) -> None:
        self.settings = settings
        self._external_connection = connection
        self.conn = connection or psycopg.connect(
            host=settings.warehouse_host,
            port=settings.warehouse_port,
            dbname=settings.warehouse_database,
            user=settings.warehouse_user,
            password=settings.warehouse_password,
            connect_timeout=15,
        )

    def close(self) -> None:
        if not self._external_connection:
            self.conn.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _table(self, name: str) -> sql.Composed:
        return sql.SQL("{}.{}").format(
            sql.Identifier(self.settings.schema_raw), sql.Identifier(name)
        )

    # -- records ------------------------------------------------------------

    def upsert(self, spec: EntitySpec, records: list[Record], source_key: str) -> tuple[int, int]:
        """Upsert records for any entity, returning (inserted, updated).

        The statement is built from the entity's declared grain rather than
        hardcoded per table — the reason every raw table has the same shape.

        Upsert rather than insert-do-nothing because these sources change:
        reference data is corrected upstream, and session results are
        *adjudicated*, amended by penalties days after publication.

        The `where payload is distinct from excluded.payload` clause is the
        subtle part. Without it every re-run would bump `updated_at` on every
        row, and "updated_at > ingested_at means this was amended upstream"
        would degrade into "means we ran the pipeline twice". The clause keeps
        that signal meaningful and makes a no-op re-run genuinely a no-op.
        """
        if not records:
            return (0, 0)

        columns = [sql.Identifier(c) for c in spec.key_columns]
        placeholders = sql.SQL(", ").join(sql.Placeholder() * (len(columns) + 2))
        table = self._table(spec.table)

        statement = sql.SQL("""
            insert into {table} ({columns}, payload, source_key)
            values ({placeholders})
            on conflict ({conflict}) do update
               set payload    = excluded.payload,
                   source_key = excluded.source_key,
                   updated_at = now()
             where {table}.payload is distinct from excluded.payload
            returning (xmax = 0) as was_insert
        """).format(
            table=table,
            columns=sql.SQL(", ").join(columns),
            placeholders=placeholders,
            conflict=sql.SQL(", ").join(columns),
        )

        inserted = updated = 0
        with self.conn.cursor() as cur:
            for record in records:
                values = [record.keys[column] for column in spec.key_columns]
                cur.execute(statement, (*values, Jsonb(record.payload), source_key))
                row = cur.fetchone()
                if row is None:
                    continue  # conflicted and payload unchanged — a true no-op
                inserted += 1 if row[0] else 0
                updated += 0 if row[0] else 1
        return inserted, updated

    def rounds_for_season(self, season: str, today: date | None = None,
                          include_unrun: bool = False) -> list[str]:
        """Rounds in a season that have actually been run, from `raw.races`.

        Race-scoped endpoints need a round per request, and the schedule is
        already in the warehouse from the reference load — so iterating rounds
        costs no extra API calls against a 500/hour budget, and the rounds are
        exactly the ones the source reports rather than a range guessed from a
        count.

        The schedule includes **future** races: `raw.races` spans 1950–2026 and
        the current season is only partly run. Requesting results for a race
        that has not happened returns an empty payload — a wasted call, and a
        checkpoint marked complete for a round that will have data later.

        The date filtering happens in `select_run_rounds` rather than in SQL so
        the rule is testable without a database.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("select round, payload->>'date' from {} "
                        "where season = %s order by round::int")
                .format(self._table("races")), (season,))
            rows = cur.fetchall()

        return select_run_rounds(rows, today or datetime.now(UTC).date(), include_unrun)

    def count(self, spec: EntitySpec) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql.SQL("select count(*) from {}").format(self._table(spec.table)))
            return cur.fetchone()[0]

    # -- dead letter --------------------------------------------------------

    def record_failure(self, entity: str, request_url: str, request_params: dict[str, Any],
                       attempt_count: int, error_class: str, error_detail: str,
                       record_payload: dict | None = None) -> None:
        """Log a failure for later retry. Must never itself raise into the run."""
        statement = sql.SQL("""
            insert into {table}
                (entity, request_url, request_params, attempt_count,
                 error_class, error_detail, record_payload)
            values (%s, %s, %s, %s, %s, %s, %s)
        """).format(table=self._table("failed_ingestions"))

        with self.conn.cursor() as cur:
            cur.execute(statement, (
                entity, request_url, Jsonb(request_params), attempt_count,
                error_class, error_detail[:2000] if error_detail else None,
                Jsonb(record_payload) if record_payload is not None else None,
            ))

    def unresolved_failures(self, entity: str | None = None) -> int:
        clause = sql.SQL("where resolved_at is null")
        params: tuple = ()
        if entity:
            clause = sql.SQL("where resolved_at is null and entity = %s")
            params = (entity,)
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("select count(*) from {} {}").format(
                    self._table("failed_ingestions"), clause), params)
            return cur.fetchone()[0]

    # -- checkpoints --------------------------------------------------------

    def get_checkpoint(self, entity: str, scope: str) -> tuple[int, str] | None:
        """Return (next_offset, status), or None if this scope is unstarted."""
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("select next_offset, status from {} where entity = %s and scope = %s")
                .format(self._table("ingestion_checkpoints")), (entity, scope))
            row = cur.fetchone()
            return (row[0], row[1]) if row else None

    def save_checkpoint(self, entity: str, scope: str, next_offset: int,
                        total_expected: int | None, status: str) -> None:
        """Record progress. Committed with the data it describes, not separately."""
        statement = sql.SQL("""
            insert into {table} (entity, scope, next_offset, total_expected, status)
            values (%s, %s, %s, %s, %s)
            on conflict (entity, scope) do update
               set next_offset    = excluded.next_offset,
                   total_expected = excluded.total_expected,
                   status         = excluded.status,
                   updated_at     = now()
        """).format(table=self._table("ingestion_checkpoints"))

        with self.conn.cursor() as cur:
            cur.execute(statement, (entity, scope, next_offset, total_expected, status))

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()


class WarehouseBudget:
    """Rate budget shared across processes, backed by `raw.api_call_log`.

    Uses its **own connection**, deliberately. Recording a call has to be
    visible to other processes immediately, which means committing — and
    committing on the pipeline's connection would also commit whatever
    half-finished upsert happened to be in flight. A second connection costs
    almost nothing and removes that coupling entirely.

    Autocommit for the same reason: a call that is recorded but uncommitted is
    a call no other process can see, which is the whole failure being fixed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.conn = psycopg.connect(
            host=settings.warehouse_host,
            port=settings.warehouse_port,
            dbname=settings.warehouse_database,
            user=settings.warehouse_user,
            password=settings.warehouse_password,
            connect_timeout=15,
            autocommit=True,
        )
        self._table = sql.SQL("{}.{}").format(
            sql.Identifier(settings.schema_raw), sql.Identifier("api_call_log"))

    def usage(self, window_seconds: int) -> tuple[int, float]:
        """(calls inside the window, seconds until the oldest leaves it).

        Both numbers come from the database's clock in a single statement, so
        they cannot disagree with each other or drift against a local clock.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                    select count(*),
                           coalesce(extract(epoch from (
                               min(called_at) + make_interval(secs => %s) - now()
                           )), 0)
                    from {} where called_at > now() - make_interval(secs => %s)
                """).format(self._table), (window_seconds, window_seconds))
            count, seconds = cur.fetchone()
            return (int(count), max(float(seconds), 0.0))

    def record(self, entity: str | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("insert into {} (entity) values (%s)").format(self._table),
                (entity,))

    def prune(self, keep_seconds: int = 7200) -> int:
        """Drop rows too old to affect the window. Returns rows removed.

        Nothing outside the trailing hour can influence the budget, so the
        table would otherwise grow without bound for no benefit. Twice the
        window is kept as headroom for inspecting a throttling incident.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("delete from {} where called_at < now() - make_interval(secs => %s)")
                .format(self._table), (keep_seconds,))
            return cur.rowcount

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> WarehouseBudget:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def select_run_rounds(rows: list[tuple[str, str | None]], today: date,
                      include_unrun: bool = False) -> list[str]:
    """Keep the rounds that have been run on or before `today`.

    A race whose date is **missing** is kept rather than skipped. The source
    has 75 years of history and an absent date is far more likely to be a gap
    in an old record than a race in the future — and skipping it would lose
    real data silently, which is the worse failure of the two.

    An unparseable date is treated the same way, for the same reason.
    """
    kept: list[str] = []

    for round_, race_date in rows:
        if include_unrun or not race_date:
            kept.append(round_)
            continue
        try:
            if date.fromisoformat(race_date) <= today:
                kept.append(round_)
        except ValueError:
            kept.append(round_)

    return kept


def parse_lake_object(content: bytes) -> dict:
    """Parse a landed object. Corruption here is a load failure, not a parse bug."""
    return json.loads(content.decode("utf-8"))
