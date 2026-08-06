"""Load the warehouse **from the lake**, plus dead-letter and checkpoint state.

ARCHITECTURE decision 2: the warehouse reads lake objects, never the API
response still held in memory. That is what makes this step independently
replayable — and it is a rule the code has to actually follow. An in-memory
shortcut would work fine today and quietly make the architecture diagram false.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ingestion.config import Settings


@dataclass(frozen=True)
class ResultRecord:
    """One row of `raw.results`, at grain (season, round, driver)."""

    season: str
    round: str
    driver_id: str
    payload: dict


@dataclass
class LoadOutcome:
    parsed: int = 0
    inserted: int = 0
    updated: int = 0
    failed: int = 0


class RecordError(ValueError):
    """A single record could not be parsed. Dead-lettered, never fatal."""


def parse_results(payload: dict) -> tuple[list[ResultRecord], list[tuple[dict, str]]]:
    """Pull result records out of one page, keeping their race context.

    Returns (records, failures). Failures carry the offending fragment and a
    reason so they can be dead-lettered rather than lost — ARCHITECTURE §5:
    one bad record must never kill a run, and must never vanish silently.

    The grain keys come from two levels: `season` and `round` live on the race,
    `driverId` on the nested result. Phase 0 measured all three at 100%
    presence across 26,115 rows, so a missing one is genuinely exceptional and
    worth recording rather than defaulting.
    """
    records: list[ResultRecord] = []
    failures: list[tuple[dict, str]] = []

    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    for race in races:
        season, rnd = race.get("season"), race.get("round")

        for result in race.get("Results", []):
            driver_id = (result.get("Driver") or {}).get("driverId")

            missing = [
                name for name, value in
                (("season", season), ("round", rnd), ("driverId", driver_id))
                if not value
            ]
            if missing:
                failures.append((result, f"missing key field(s): {', '.join(missing)}"))
                continue

            records.append(ResultRecord(
                season=str(season), round=str(rnd), driver_id=str(driver_id),
                payload=result,
            ))

    return records, failures


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

    # -- results ------------------------------------------------------------

    def upsert_results(self, records: list[ResultRecord], source_key: str) -> tuple[int, int]:
        """Upsert records, returning (inserted, updated).

        Upsert rather than insert-do-nothing because F1 results are
        *adjudicated*: a penalty or appeal amends a published result days
        later, and do-nothing would freeze the pre-penalty version.

        The `where payload is distinct from excluded.payload` clause is the
        subtle part. Without it, every re-run would bump `updated_at` on every
        row, and "updated_at > ingested_at means the sport amended this result"
        would degrade into "means we ran the pipeline twice". The clause keeps
        that signal meaningful, and makes a no-op re-run genuinely a no-op.
        """
        if not records:
            return (0, 0)

        statement = sql.SQL("""
            insert into {table} (season, round, driver_id, payload, source_key)
            values (%s, %s, %s, %s, %s)
            on conflict (season, round, driver_id) do update
               set payload    = excluded.payload,
                   source_key = excluded.source_key,
                   updated_at = now()
             where {table}.payload is distinct from excluded.payload
            returning (xmax = 0) as was_insert
        """).format(table=self._table("results"))

        inserted = updated = 0
        with self.conn.cursor() as cur:
            for record in records:
                cur.execute(statement, (
                    record.season, record.round, record.driver_id,
                    Jsonb(record.payload), source_key,
                ))
                row = cur.fetchone()
                if row is None:
                    continue  # conflicted and payload unchanged — a true no-op
                inserted += 1 if row[0] else 0
                updated += 0 if row[0] else 1
        return inserted, updated

    def count_results(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql.SQL("select count(*) from {}").format(self._table("results")))
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


def parse_lake_object(content: bytes) -> dict:
    """Parse a landed object. Corruption here is a load failure, not a parse bug."""
    return json.loads(content.decode("utf-8"))
