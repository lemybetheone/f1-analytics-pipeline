"""Orchestrate extract → land → load. Implements none of them.

Two phases, deliberately separate:

1. **Extract and land.** Page through the API, write each page to the lake,
   checkpoint after every page. This is the expensive, rate-limited half — the
   one that must survive interruption, because a full backfill is ~3,925 calls
   against a 500/hour budget and therefore about eight hours.

2. **Load.** List the landed objects, read them *back from the lake*, and
   upsert. Nothing here touches the API, so it can be re-run freely — that is
   the point of landing raw first.

Running phase 2 alone re-loads the warehouse from existing lake objects at zero
API cost, which is what "replayable" means in practice.

Usage
-----
    python -m ingestion.pipeline --entity results --season 2024
    python -m ingestion.pipeline --entity results --season 2024 --load-only
    python -m ingestion.pipeline --entity results --season 2024 --resume
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from ingestion.config import ConfigError, Settings, load
from ingestion.extract import ExtractError, Extractor
from ingestion.load_lake import Lake
from ingestion.load_warehouse import (
    LoadOutcome,
    Warehouse,
    parse_lake_object,
    parse_results,
)

# Only `results` for now. Decision 22: prove one endpoint through the whole
# path before generalising, because the risk is the vertical path rather than
# the twelfth extractor.
ENTITY_PATHS = {
    "results": "{season}/results",
}


def extract_and_land(settings: Settings, warehouse: Warehouse, entity: str,
                     season: str, resume: bool, ingestion_date: str) -> list[str]:
    """Page through the API and land each page. Returns the keys written."""
    scope = f"season={season}"
    path = ENTITY_PATHS[entity].format(season=season)

    start_offset = 0
    if resume:
        checkpoint = warehouse.get_checkpoint(entity, scope)
        if checkpoint and checkpoint[1] == "complete":
            print(f"  {scope} already complete — nothing to extract")
            return []
        if checkpoint:
            start_offset = checkpoint[0]
            print(f"  resuming {scope} from offset {start_offset}")

    extractor = Extractor(settings)
    lake = Lake(settings)
    written: list[str] = []
    offset = start_offset

    while True:
        try:
            page = extractor.fetch_page(path, offset=offset)
        except ExtractError as exc:
            # The request is dead after retries. Record it and stop this scope
            # rather than pressing on: a gap in the middle of a paginated
            # backfill is worse than a short one you can resume.
            warehouse.record_failure(
                entity=entity,
                request_url=f"{settings.source_base_url}/{path}.json",
                request_params={"limit": 100, "offset": offset},
                attempt_count=4,
                error_class=type(exc).__name__,
                error_detail=str(exc),
            )
            warehouse.save_checkpoint(entity, scope, offset, None, "failed")
            warehouse.commit()
            raise

        key = lake.key_for(entity, scope, page.offset, ingestion_date)
        lake.put(key, page.content)
        written.append(key)

        next_offset = page.offset + page.limit
        done = page.is_last

        # Checkpoint after the object is durably in the lake, never before. If
        # the process dies between the two, the worst case is re-fetching one
        # page — the opposite order would skip it entirely.
        warehouse.save_checkpoint(
            entity, scope, next_offset, page.total, "complete" if done else "in_progress")
        warehouse.commit()

        print(f"  landed {key}  ({page.offset + page.limit if not done else page.total}"
              f"/{page.total} rows)")

        if done:
            break
        offset = next_offset

    return written


def load_from_lake(settings: Settings, warehouse: Warehouse, entity: str,
                   season: str, ingestion_date: str) -> LoadOutcome:
    """Read landed objects back out of the lake and upsert them."""
    lake = Lake(settings)
    scope = f"season={season}"
    prefix = f"{settings.lake_prefix}/{entity}/{ingestion_date}/{entity}_{scope}_"

    keys = lake.list_keys(prefix)
    if not keys:
        print(f"  no lake objects under {prefix}")
        return LoadOutcome()

    outcome = LoadOutcome()
    for key in keys:
        content = lake.get(key)
        try:
            payload = parse_lake_object(content)
        except ValueError as exc:
            warehouse.record_failure(
                entity=entity, request_url=key, request_params={"key": key},
                attempt_count=1, error_class=type(exc).__name__, error_detail=str(exc))
            outcome.failed += 1
            continue

        records, failures = parse_results(payload)

        # Record-level failures are dead-lettered and the run continues.
        for fragment, reason in failures:
            warehouse.record_failure(
                entity=entity, request_url=key, request_params={"key": key},
                attempt_count=1, error_class="RecordError", error_detail=reason,
                record_payload=fragment)
            outcome.failed += 1

        inserted, updated = warehouse.upsert_results(records, source_key=key)
        outcome.parsed += len(records)
        outcome.inserted += inserted
        outcome.updated += updated
        print(f"  {key}: {len(records)} parsed, {inserted} inserted, {updated} updated")

    warehouse.commit()
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", default="results", choices=sorted(ENTITY_PATHS))
    parser.add_argument("--season", required=True)
    parser.add_argument("--load-only", action="store_true",
                        help="skip the API entirely; reload from existing lake objects")
    parser.add_argument("--resume", action="store_true",
                        help="continue from the recorded checkpoint")
    parser.add_argument("--ingestion-date", default=None,
                        help="lake partition to write or read (default: today, UTC)")
    args = parser.parse_args(argv)

    try:
        settings = load()
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        return 1

    ingestion_date = args.ingestion_date or datetime.now(UTC).strftime("%Y-%m-%d")

    print(f"entity={args.entity} season={args.season} env={settings.target_env}")
    print(f"warehouse: {settings.dsn_description}")
    print(f"lake:      s3://{settings.lake_bucket}/{settings.lake_prefix}")
    print(f"partition: {ingestion_date}\n")

    with Warehouse(settings) as warehouse:
        if not args.load_only:
            print("Extract and land")
            try:
                extract_and_land(settings, warehouse, args.entity, args.season,
                                 args.resume, ingestion_date)
            except ExtractError as exc:
                print(f"\nExtraction failed: {exc}", file=sys.stderr)
                print("Recorded in raw.failed_ingestions; re-run with --resume.",
                      file=sys.stderr)
                return 1
            print()

        print("Load from lake")
        outcome = load_from_lake(settings, warehouse, args.entity, args.season,
                                 ingestion_date)

        total = warehouse.count_results()
        unresolved = warehouse.unresolved_failures(args.entity)

    print(f"\n  parsed {outcome.parsed}, inserted {outcome.inserted}, "
          f"updated {outcome.updated}, failed {outcome.failed}")
    print(f"  raw.results now holds {total} rows")
    if unresolved:
        print(f"  ! {unresolved} unresolved dead-letter rows — investigate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
