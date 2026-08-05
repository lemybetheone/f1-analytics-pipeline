"""Phase 0 — classify each source: immutable event, mutable reference, or snapshot.

Why this exists
---------------
ARCHITECTURE §4 calls this the most important design question, and the one that
cannot be retrofitted: an overwrite upsert discards the previous value, so any
attribute whose history matters must be append-only *from the first load*. By
the time you discover you needed the history, it is gone.

The decisive empirical question is **constructor rebrands**. PRD §6 locks
`dim_constructor` as SCD Type 2 so that Toro Rosso → AlphaTauri → RB is
queryable as one team's identity history. That only holds if the source mutates
a constructor's *name* while keeping its id. If instead the source issues a
distinct `constructorId` per brand, they are separate entities upstream, SCD2
has nothing to track, and the locked decision needs revisiting.

This script walks the constructor list season by season and reports:

* every `constructorId` whose name changed between seasons — the SCD2 case
* the ids present in each season, so a rebrand that appears as an id swap is
  visible as one id disappearing exactly when another appears

Usage
-----
    python discovery/probe_mutability.py
    python discovery/probe_mutability.py --from-season 1990 --to-season 2024
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_source import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_DELAY_SECONDS,
    USER_AGENT,
    budget_warning,
    collect_records,
    fetch,
)

# Rebrand chains to look for explicitly. Each is a real-world team whose
# identity continued under a new name — precisely the history SCD2 would model.
KNOWN_REBRANDS: tuple[tuple[str, ...], ...] = (
    ("toro_rosso", "alphatauri", "rb"),
    ("renault", "alpine"),
    ("force_india", "racing_point", "aston_martin"),
    ("sauber", "bmw_sauber", "alfa"),
    ("jordan", "midland", "spyker", "spyker_mf1"),
    ("stewart", "jaguar", "red_bull"),
    ("tyrrell", "bar", "honda", "brawn", "mercedes"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--out", default="discovery/findings")
    parser.add_argument("--from-season", type=int, default=1996)
    parser.add_argument("--to-season", type=int, default=2024)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    seasons = list(range(args.from_season, args.to_season + 1))
    print(f"Walking constructors for {len(seasons)} seasons "
          f"({args.from_season}–{args.to_season})\n")

    names_by_id: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    ids_by_season: dict[int, set[str]] = {}

    for season in seasons:
        resp = fetch(session, f"{args.base_url}/{season}/constructors.json",
                     {"limit": 100, "offset": 0}, args.delay)
        if not resp.ok:
            print(f"  {season}: FAILED — {resp.error}")
            continue

        rows = collect_records(resp.payload, "MRData.ConstructorTable.Constructors")
        ids_by_season[season] = {r.get("constructorId") for r in rows}
        for row in rows:
            names_by_id[row.get("constructorId")][row.get("name")].append(season)
        print(f"  {season}: {len(rows)} constructors")

    # An id carrying more than one name over time is a mutation in place, and
    # is exactly what SCD Type 2 exists to record.
    renamed = {
        cid: {name: (min(yrs), max(yrs)) for name, yrs in names.items()}
        for cid, names in names_by_id.items() if len(names) > 1
    }

    # A rebrand modelled as separate ids shows up as one id ending and the
    # next beginning — no overlap, contiguous in time.
    chains = []
    for chain in KNOWN_REBRANDS:
        present = [c for c in chain if c in names_by_id]
        if len(present) < 2:
            continue
        chains.append({
            "chain": present,
            "spans": {
                cid: {
                    "name": next(iter(names_by_id[cid])),
                    "first_season": min(s for yrs in names_by_id[cid].values() for s in yrs),
                    "last_season": max(s for yrs in names_by_id[cid].values() for s in yrs),
                }
                for cid in present
            },
        })

    report = {
        "probed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "seasons": [args.from_season, args.to_season],
        "distinct_constructor_ids": len(names_by_id),
        "ids_with_more_than_one_name": renamed,
        "rebrand_chains_as_separate_ids": chains,
    }

    json_path = out_dir / "mutability.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"\n  Distinct constructorIds seen: {len(names_by_id)}")
    print(f"  ids carrying more than one name over time: {len(renamed)}")
    for cid, names in renamed.items():
        print(f"    {cid}: {names}")

    print("\n  Known rebrands modelled as separate ids:")
    for chain in chains:
        parts = " -> ".join(
            f"{cid} ({s['name']}, {s['first_season']}-{s['last_season']})"
            for cid, s in chain["spans"].items()
        )
        print(f"    {parts}")

    if warning := budget_warning():
        print(f"\n  ! {warning}")
    print(f"\n  {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
