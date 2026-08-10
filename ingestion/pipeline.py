"""Orchestrate extract → land → load. Implements none of them.

Two phases, deliberately separate:

1. **Extract and land.** Page through the API, write each page to the lake,
   checkpoint after every page. This is the expensive, rate-limited half — the
   one that must survive interruption, because a full backfill is thousands of
   calls against a 500/hour budget.

2. **Load.** List the landed objects, read them *back from the lake*, and
   upsert. Nothing here touches the API, so it can be re-run freely — that is
   the point of landing raw first.

`--load-only` re-loads the warehouse from existing lake objects at zero API
cost, which is what "replayable" means in practice.

Usage
-----
    python -m ingestion.pipeline --entity results --season 2024
    python -m ingestion.pipeline --entity drivers
    python -m ingestion.pipeline --all-reference
    python -m ingestion.pipeline --entity results --season 2024 --load-only
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from ingestion.config import ConfigError, Settings, load
from ingestion.entities import (
    ENTITIES,
    SCOPE_GLOBAL,
    SCOPE_RACE,
    SCOPE_SEASON,
    EntitySpec,
    parse_records,
)
from ingestion.extract import ExtractError, Extractor
from ingestion.load_lake import Lake
from ingestion.load_warehouse import (
    LoadOutcome,
    Warehouse,
    WarehouseBudget,
    parse_lake_object,
)


def extract_and_land(settings: Settings, warehouse: Warehouse, spec: EntitySpec,
                     season: str | None, resume: bool, ingestion_date: str,
                     extractor: Extractor, lake: Lake,
                     round_: str | None = None) -> list[str]:
    """Page through the API and land each page. Returns the keys written."""
    scope = spec.scope_label(season, round_)
    path = spec.path_for(season, round_)

    # Tag calls with the entity spending them, so the log can answer "what
    # consumed the allowance" rather than only "the allowance was consumed".
    extractor.entity = spec.name

    offset = 0
    if resume:
        checkpoint = warehouse.get_checkpoint(spec.name, scope)
        if checkpoint and checkpoint[1] == "complete":
            print(f"    {scope} already complete — skipping extract")
            return []
        if checkpoint:
            offset = checkpoint[0]
            print(f"    resuming from offset {offset}")

    written: list[str] = []

    while True:
        try:
            page = extractor.fetch_page(path, offset=offset)
        except ExtractError as exc:
            # Dead after retries. Record it and stop this scope rather than
            # pressing on: a gap in the middle of a paginated backfill is worse
            # than a short one you can resume.
            warehouse.record_failure(
                entity=spec.name,
                request_url=f"{settings.source_base_url}/{path}.json",
                request_params={"limit": 100, "offset": offset},
                attempt_count=4, error_class=type(exc).__name__, error_detail=str(exc),
            )
            warehouse.save_checkpoint(spec.name, scope, offset, None, "failed")
            warehouse.commit()
            raise

        key = lake.key_for(spec.name, scope, page.offset, ingestion_date)
        lake.put(key, page.content)
        written.append(key)

        next_offset = page.offset + page.limit
        done = page.is_last or page.total == 0

        # Checkpoint only after the object is durably in the lake. If the
        # process dies between the two, the worst case is re-fetching one page;
        # the opposite order would skip it entirely.
        warehouse.save_checkpoint(
            spec.name, scope, next_offset, page.total,
            "complete" if done else "in_progress")
        warehouse.commit()

        print(f"    landed {key.rsplit('/', 1)[-1]}  "
              f"({min(next_offset, page.total)}/{page.total} rows)")

        if done:
            break
        offset = next_offset

    return written


def load_from_lake(settings: Settings, warehouse: Warehouse, spec: EntitySpec,
                   season: str | None, ingestion_date: str, lake: Lake,
                   round_: str | None = None) -> LoadOutcome:
    """Read landed objects back out of the lake and upsert them.

    For race-scoped entities `round_` may be None, which loads every round in
    the partition at once — the prefix simply stops at the season. That keeps
    `--load-only` a single pass over the lake rather than one listing per round.
    """
    if spec.scope == SCOPE_RACE and round_ is None:
        scope_prefix = f"season={season}_round="
    else:
        scope_prefix = spec.scope_label(season, round_) + "_"
    prefix = f"{settings.lake_prefix}/{spec.name}/{ingestion_date}/{spec.name}_{scope_prefix}"

    keys = lake.list_keys(prefix)
    if not keys:
        print(f"    no lake objects under {prefix}")
        return LoadOutcome()

    outcome = LoadOutcome()
    for key in keys:
        try:
            payload = parse_lake_object(lake.get(key))
        except ValueError as exc:
            warehouse.record_failure(
                entity=spec.name, request_url=key, request_params={"key": key},
                attempt_count=1, error_class=type(exc).__name__, error_detail=str(exc))
            outcome.failed += 1
            continue

        records, failures = parse_records(spec, payload)

        # Record-level failures are dead-lettered and the run continues.
        for fragment, reason in failures:
            warehouse.record_failure(
                entity=spec.name, request_url=key, request_params={"key": key},
                attempt_count=1, error_class="RecordError", error_detail=reason,
                record_payload=fragment)
            outcome.failed += 1

        inserted, updated = warehouse.upsert(spec, records, source_key=key)
        outcome.parsed += len(records)
        outcome.inserted += inserted
        outcome.updated += updated

    warehouse.commit()
    return outcome


def run_entity(settings: Settings, warehouse: Warehouse, spec: EntitySpec,
               season: str | None, args, ingestion_date: str,
               extractor: Extractor, lake: Lake) -> LoadOutcome | None:
    label = f"{spec.name}" + (f" season={season}" if season else "")
    print(f"\n{label}")

    if not args.load_only:
        # Race-scoped entities need one request per round. The schedule comes
        # from raw.races, already loaded, so this costs no API calls — and the
        # rounds are the ones the source actually reports rather than a range
        # inferred from a count.
        rounds: list[str | None]
        if spec.scope == SCOPE_RACE:
            rounds = list(warehouse.rounds_for_season(season))
            scheduled = len(warehouse.rounds_for_season(season, include_unrun=True))
            if not rounds:
                if scheduled:
                    print(f"    {scheduled} rounds scheduled for {season}, none run yet "
                          "— nothing to fetch")
                    return LoadOutcome()
                print(f"    no rounds in raw.races for {season} — "
                      "load the reference entities first (--all-reference)",
                      file=sys.stderr)
                return None
            # Say so when rounds are skipped. A silently shorter run looks
            # identical to a complete one.
            if scheduled > len(rounds):
                print(f"    {len(rounds)} of {scheduled} rounds run "
                      f"({scheduled - len(rounds)} not yet raced, skipped)")
            else:
                print(f"    {len(rounds)} rounds")
        else:
            rounds = [None]

        for round_ in rounds:
            try:
                extract_and_land(settings, warehouse, spec, season, args.resume,
                                 ingestion_date, extractor, lake, round_)
            except ExtractError as exc:
                print(f"    FAILED at round {round_}: {exc}", file=sys.stderr)
                print("    Recorded in raw.failed_ingestions; re-run with --resume.",
                      file=sys.stderr)
                return None

    outcome = load_from_lake(settings, warehouse, spec, season, ingestion_date, lake)
    print(f"    parsed {outcome.parsed}, inserted {outcome.inserted}, "
          f"updated {outcome.updated}, failed {outcome.failed} "
          f"→ raw.{spec.table} holds {warehouse.count(spec)}")
    return outcome


def use_utf8_console() -> None:
    """Stop console output dying on a non-UTF-8 terminal.

    Windows consoles default to cp1252, which cannot encode the arrows and
    em-dashes used in progress output — a `UnicodeEncodeError` that kills an
    otherwise healthy run at the moment it tries to report success. `replace`
    degrades those characters to `?` rather than raising, because a slightly
    ugly log beats a crashed pipeline.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", choices=sorted(ENTITIES),
                        help="a single entity to ingest")
    parser.add_argument("--all-reference", action="store_true",
                        help="every global-scope entity (seasons, circuits, drivers, "
                             "constructors, status, races)")
    parser.add_argument("--all-season", action="store_true",
                        help="every season-scoped entity; requires --season")
    parser.add_argument("--all-race", action="store_true",
                        help="every race-scoped entity (pit stops, both standings); "
                             "requires --season and a populated raw.races")
    parser.add_argument("--season", help="season for season-scoped entities")
    parser.add_argument("--load-only", action="store_true",
                        help="skip the API entirely; reload from existing lake objects")
    parser.add_argument("--resume", action="store_true",
                        help="continue from the recorded checkpoint")
    parser.add_argument("--ingestion-date", default=None,
                        help="lake partition to write or read (default: today, UTC)")
    args = parser.parse_args(argv)

    if not (args.entity or args.all_reference or args.all_season or args.all_race):
        parser.error("choose --entity, --all-reference, --all-season or --all-race")

    selected: list[EntitySpec] = []
    if args.entity:
        selected.append(ENTITIES[args.entity])
    if args.all_reference:
        selected += [s for s in ENTITIES.values() if s.scope == SCOPE_GLOBAL]
    if args.all_season:
        selected += [s for s in ENTITIES.values() if s.scope == SCOPE_SEASON]
    if args.all_race:
        selected += [s for s in ENTITIES.values() if s.scope == SCOPE_RACE]

    needs_season = [s.name for s in selected if s.scope in (SCOPE_SEASON, SCOPE_RACE)]
    if needs_season and not args.season:
        parser.error(f"--season is required for: {', '.join(needs_season)}")

    try:
        settings = load()
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 1

    ingestion_date = args.ingestion_date or datetime.now(UTC).strftime("%Y-%m-%d")

    print(f"entities:  {', '.join(s.name for s in selected)}")
    print(f"warehouse: {settings.dsn_description}")
    print(f"lake:      s3://{settings.lake_bucket}/{settings.lake_prefix}")
    print(f"partition: {ingestion_date}")

    # The budget lives in the warehouse, not in this process. A limiter per
    # process would let each of six subprocesses spend the full hourly
    # allowance, and a resumed backfill would start its count from zero.
    budget = WarehouseBudget(settings)
    pruned = budget.prune()
    if pruned:
        print(f"pruned {pruned} api_call_log rows older than the window")

    extractor = Extractor(settings, budget=budget)
    lake = Lake(settings)
    failed: list[str] = []

    with budget, Warehouse(settings) as warehouse:
        for spec in selected:
            season = args.season if spec.scope in (SCOPE_SEASON, SCOPE_RACE) else None
            if run_entity(settings, warehouse, spec, season, args,
                          ingestion_date, extractor, lake) is None:
                failed.append(spec.name)

        unresolved = warehouse.unresolved_failures()
        # Read inside the block: the budget owns a connection that closes on
        # exit, and this figure comes from the shared log rather than from
        # this process's own count.
        calls_used = extractor.limiter.used_in_window

    print(f"\napi calls used: {calls_used}/{settings.requests_per_hour} "
          "in the last hour (all processes)")
    if unresolved:
        print(f"! {unresolved} unresolved dead-letter rows — investigate")
    if failed:
        print(f"! failed entities: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
