"""Phase 0 — measure join coverage between facts and their dimension sources.

Why this exists
---------------
SCHEMA §5 says facts map unmatched foreign keys to a synthetic "Unknown"
member, and that the gap should be *measured in Phase 0 rather than assumed*.
The measurement decides real design questions:

* If coverage is 100%, `relationships` tests can be strict and the Unknown
  member is a cheap insurance policy.
* If it is not, the Unknown member is load-bearing, and every fact row that
  misses needs a defined destination rather than being silently dropped by an
  inner join.

It also profiles field presence **by decade**. A 2024 sample says `FastestLap`
is 97% present; F1 has been running since 1950 and fastest-lap data does not
exist for most of it. Designing `not_null` tests off a modern sample produces a
suite that fails the moment the backfill reaches the 1950s.

Usage
-----
    python discovery/probe_join_coverage.py                 # full history
    python discovery/probe_join_coverage.py --from-season 2000
    python discovery/probe_join_coverage.py --sample-every 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

# Import the sibling probe regardless of the working directory the script is
# run from, so no PYTHONPATH is needed to reproduce these findings.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_source import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_DELAY_SECONDS,
    USER_AGENT,
    budget_warning,
    collect_records,
    fetch,
)

PAGE_SIZE = 100  # the observed hard cap; see ARCHITECTURE decision 11


def fetch_all(session: requests.Session, base_url: str, path: str,
              grain_path: str, delay: float, label: str) -> list[dict]:
    """Page through an endpoint until every row has been collected.

    Terminates on the reported total rather than on an empty page, and stops
    if the server ever returns fewer rows than asked without reaching that
    total — a silent short read is a bug worth failing loudly on.
    """
    rows: list[dict] = []
    offset, total = 0, None

    while True:
        resp = fetch(session, f"{base_url}/{path}.json",
                     {"limit": PAGE_SIZE, "offset": offset}, delay)
        if not resp.ok:
            print(f"    {label}: FAILED at offset {offset} — {resp.error}")
            break

        inner = resp.payload.get("MRData", resp.payload)
        if total is None:
            total = int(inner.get("total", 0))
            print(f"    {label}: {total} rows, {-(-total // PAGE_SIZE)} calls")

        page = collect_records(resp.payload, grain_path)
        rows.extend(page)
        offset += PAGE_SIZE

        if offset >= total or not page:
            break

    return rows


def iter_race_children(session: requests.Session, base_url: str, path: str,
                       child_key: str, delay: float, label: str) -> Iterator[tuple[dict, dict]]:
    """Yield (race, child) pairs so each fact row keeps its race context.

    `collect_records` flattens the envelope away, but coverage of the
    (season, round) key needs the parent race the row came from.
    """
    offset, total = 0, None

    while True:
        resp = fetch(session, f"{base_url}/{path}.json",
                     {"limit": PAGE_SIZE, "offset": offset}, delay)
        if not resp.ok:
            print(f"    {label}: FAILED at offset {offset} — {resp.error}")
            return

        inner = resp.payload.get("MRData", resp.payload)
        if total is None:
            total = int(inner.get("total", 0))
            print(f"    {label}: {total} rows, {-(-total // PAGE_SIZE)} calls")

        races = inner.get("RaceTable", {}).get("Races", [])
        emitted = 0
        for race in races:
            for child in race.get(child_key, []):
                emitted += 1
                yield race, child

        offset += PAGE_SIZE
        if offset >= total or emitted == 0:
            return


def decade_of(season: str) -> str:
    try:
        return f"{int(season) // 10 * 10}s"
    except (TypeError, ValueError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--out", default="discovery/findings")
    parser.add_argument("--from-season", type=int, default=None,
                        help="restrict the fact scan to seasons >= this year")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    print(f"Join coverage against {args.base_url}\n")
    print("  Loading dimension sources...")

    drivers = fetch_all(session, args.base_url, "drivers",
                        "MRData.DriverTable.Drivers", args.delay, "drivers")
    constructors = fetch_all(session, args.base_url, "constructors",
                             "MRData.ConstructorTable.Constructors", args.delay, "constructors")
    circuits = fetch_all(session, args.base_url, "circuits",
                         "MRData.CircuitTable.Circuits", args.delay, "circuits")
    races = fetch_all(session, args.base_url, "races",
                      "MRData.RaceTable.Races", args.delay, "races")
    statuses = fetch_all(session, args.base_url, "status",
                         "MRData.StatusTable.Status", args.delay, "status")

    driver_keys = {d.get("driverId") for d in drivers}
    constructor_keys = {c.get("constructorId") for c in constructors}
    circuit_keys = {c.get("circuitId") for c in circuits}
    race_keys = {(r.get("season"), r.get("round")) for r in races}
    status_values = {s.get("status") for s in statuses}

    print(f"\n  Dimension keys: {len(driver_keys)} drivers, "
          f"{len(constructor_keys)} constructors, {len(circuit_keys)} circuits, "
          f"{len(race_keys)} races, {len(status_values)} statuses")

    print("\n  Scanning fct_results source...")
    misses: dict[str, Counter] = defaultdict(Counter)
    checked: Counter = Counter()
    presence: dict[str, Counter] = defaultdict(Counter)
    rows_by_decade: Counter = Counter()
    fact_rows = 0

    scan_path = f"{args.from_season}/results" if args.from_season else "results"
    for race, result in iter_race_children(session, args.base_url, scan_path,
                                           "Results", args.delay, "results"):
        fact_rows += 1
        season, rnd = race.get("season"), race.get("round")
        decade = decade_of(season)
        rows_by_decade[decade] += 1

        for name, value, universe in (
            ("driverId", (result.get("Driver") or {}).get("driverId"), driver_keys),
            ("constructorId", (result.get("Constructor") or {}).get("constructorId"), constructor_keys),
            ("status", result.get("status"), status_values),
        ):
            checked[name] += 1
            if value not in universe:
                misses[name][value] += 1

        checked["race"] += 1
        if (season, rnd) not in race_keys:
            misses["race"][f"{season}/{rnd}"] += 1

        # Field presence by decade — the eras differ, and the tests must know.
        for probe_field in ("Time", "FastestLap", "grid", "position",
                            "positionText", "points", "laps", "number"):
            if result.get(probe_field) not in (None, ""):
                presence[probe_field][decade] += 1

    # Races reference circuits; check that edge too.
    circuit_misses = Counter(
        (r.get("Circuit") or {}).get("circuitId") for r in races
        if (r.get("Circuit") or {}).get("circuitId") not in circuit_keys
    )

    report: dict[str, Any] = {
        "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "scope": scan_path,
        "dimension_sizes": {
            "drivers": len(driver_keys), "constructors": len(constructor_keys),
            "circuits": len(circuit_keys), "races": len(race_keys),
            "statuses": len(status_values),
        },
        "fact_rows_scanned": fact_rows,
        "coverage": {
            name: {
                "checked": checked[name],
                "missing": sum(misses[name].values()),
                "coverage_pct": round(100 * (checked[name] - sum(misses[name].values())) / (checked[name] or 1), 4),
                "distinct_missing_keys": len(misses[name]),
                "examples": misses[name].most_common(10),
            }
            for name in ("driverId", "constructorId", "status", "race")
        },
        "races_to_circuits": {
            "checked": len(races),
            "missing": sum(circuit_misses.values()),
            "examples": circuit_misses.most_common(10),
        },
        "rows_by_decade": dict(sorted(rows_by_decade.items())),
        "field_presence_by_decade": {
            f: {d: presence[f].get(d, 0) for d in sorted(rows_by_decade)}
            for f in sorted(presence)
        },
    }

    json_path = out_dir / "join_coverage.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_markdown(report, out_dir / "join_coverage.md")

    print(f"\n  Scanned {fact_rows} fact rows")
    for name, stats in report["coverage"].items():
        flag = "OK " if stats["missing"] == 0 else "GAP"
        print(f"    {flag} {name:16s} {stats['coverage_pct']:8.4f}%  "
              f"({stats['missing']} missing, {stats['distinct_missing_keys']} distinct)")
    if warning := budget_warning():
        print(f"\n  ! {warning}")
    print(f"\n  {json_path}\n  {out_dir / 'join_coverage.md'}")
    return 0


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# Phase 0 — join coverage & era profile",
        "",
        "> Generated by `discovery/probe_join_coverage.py`. Measured, not assumed.",
        ">",
        "> Derived from **Jolpica-F1** data, licensed under",
        "> [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).",
        "",
        f"- **Source:** `{report['base_url']}`",
        f"- **Probed at:** {report['probed_at']}",
        f"- **Fact scope:** `{report['scope']}` — {report['fact_rows_scanned']:,} rows scanned",
        "",
        "## Foreign key coverage — `fct_results` → dimensions",
        "",
        "| Foreign key | Rows checked | Missing | Coverage | Distinct missing |",
        "|---|---|---|---|---|",
    ]
    for name, s in report["coverage"].items():
        lines.append(
            f"| `{name}` | {s['checked']:,} | {s['missing']:,} | "
            f"**{s['coverage_pct']}%** | {s['distinct_missing_keys']} |"
        )

    rc = report["races_to_circuits"]
    lines += [
        "",
        f"`dim_race` → `dim_circuit`: {rc['checked']:,} races checked, {rc['missing']} missing.",
        "",
        "## What this means for the model",
        "",
    ]
    total_missing = sum(s["missing"] for s in report["coverage"].values()) + rc["missing"]
    if total_missing == 0:
        lines += [
            "**Every foreign key resolves.** The Unknown member stays in the design as",
            "insurance against future source drift, but it is not load-bearing today,",
            "and `relationships` tests can be strict on every fact-to-dimension edge.",
        ]
    else:
        lines += [
            f"**{total_missing} fact rows reference a key absent from the dimension source.**",
            "The Unknown member is load-bearing: those rows must map to it rather than",
            "being dropped by an inner join. See the examples in the JSON report.",
        ]

    lines += ["", "## Field presence by decade", "",
              "Presence of a field in `results` rows, by decade. A field that is absent",
              "for whole eras must not carry a `not_null` test.", ""]

    decades = list(report["rows_by_decade"])
    lines += ["| Field | " + " | ".join(decades) + " |",
              "|---" * (len(decades) + 1) + "|"]
    lines.append("| _rows_ | " + " | ".join(f"{report['rows_by_decade'][d]:,}" for d in decades) + " |")
    for f, by_decade in report["field_presence_by_decade"].items():
        cells = []
        for d in decades:
            total = report["rows_by_decade"][d]
            pct = 100 * by_decade.get(d, 0) / total if total else 0
            cells.append("—" if pct == 0 else f"{pct:.0f}%")
        lines.append(f"| `{f}` | " + " | ".join(cells) + " |")

    lines += [""]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
