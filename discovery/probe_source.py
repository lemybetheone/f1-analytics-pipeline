"""Phase 0 discovery probe — validate a candidate F1 source API against reality.

Why this exists
---------------
SCHEMA.md's Phase 0 gate forbids designing a single table from documentation or
assumption. Container types, date representations, nullability, row caps and
rate limits are routinely different from what docs imply, and a schema built on
a wrong assumption is the most expensive kind of rework. This script answers
those questions by *calling the API and reporting what came back*.

It is deliberately throwaway. It does not import from `ingestion/`, and
`ingestion/` must never import from it — see the note on ARCHITECTURE §10 in
the decision log. Its only output is evidence: a JSON record of every response
and a Markdown summary sized to paste into SCHEMA.md's Phase 0 table.

What it reports, per endpoint
-----------------------------
* HTTP status, latency, and the response headers (rate-limit headers included)
* The response envelope — discovered by walking the payload, not hardcoded
* Every field path, with how often it is present, how often it is null, and
  every type observed. This is nullability *in practice*, which is the only
  kind that matters for a primary key.
* Values that arrive as strings but are really numbers, dates or times, which
  is what drives the "types that need casting" column in SCHEMA.
* Per-call row caps and whether `limit`/`offset` pagination actually works.

Usage
-----
    python discovery/probe_source.py                     # probe every endpoint
    python discovery/probe_source.py --only seasons drivers
    python discovery/probe_source.py --base-url https://... --delay 1.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Jolpica-F1, the community successor to the deprecated Ergast API.
# PRD §1 lists it as the leading candidate; this run is what confirms it.
DEFAULT_BASE_URL = "https://api.jolpi.ca/ergast/f1"

# A season with a full set of sessions, recent enough to exercise sprint
# weekends and modern status codes, but complete so no endpoint returns a
# partial year.
PROBE_SEASON = "2024"
PROBE_ROUND = "1"

# Sprint sessions only happen on some weekends, so the sprint endpoint must be
# probed on a round that actually had one (2024 round 5, China). Probing round
# 1 returns a 200 with an empty payload, which looks like a broken endpoint and
# is really just an empty result set — a distinction worth getting right.
PROBE_SPRINT_ROUND = "5"

# Documented limits (confirmed 2026-08-03): 4 requests/second burst,
# 500 requests/hour sustained, unauthenticated. The API returns no rate-limit
# headers, so neither figure can be read at runtime.
#
# The hourly budget is what binds. 0.6s pacing is 1.67 req/s — comfortably
# inside the burst limit, and fine for a probe run of a few hundred calls, but
# it implies ~6,000 requests/hour. Anything long-running must pace to
# SUSTAINED_DELAY_SECONDS instead, or budget in hourly windows.
BURST_LIMIT_PER_SECOND = 4
SUSTAINED_LIMIT_PER_HOUR = 500
SUSTAINED_DELAY_SECONDS = 3600 / SUSTAINED_LIMIT_PER_HOUR  # 7.2s

DEFAULT_DELAY_SECONDS = 0.6

# Retry policy mirrors ARCHITECTURE §5: transient failures are retried with
# exponential backoff, non-transient ones fail fast and are recorded.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 1.5

# How many records to pull when profiling fields. Nullability cannot be
# established from one record, and a whole season is more than enough.
PROFILE_LIMIT = 100

# Deliberately larger than any plausible cap, to discover the real one.
CAP_PROBE_LIMIT = 2000

USER_AGENT = "f1-analytics-pipeline/phase0-discovery (+portfolio project)"


@dataclass(frozen=True)
class Endpoint:
    """A candidate endpoint and the analytical question that justifies it."""

    name: str
    path: str
    serves: str  # which PRD §6 theme needs it — no endpoint without a reason


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("seasons", "seasons", "§5 season context; the season dimension"),
    Endpoint("circuits", "circuits", "§5 circuit rollups; dim_circuit"),
    Endpoint("races", f"{PROBE_SEASON}/races", "§5 dim_race (season, round, date)"),
    Endpoint("drivers", f"{PROBE_SEASON}/drivers", "§2 dim_driver"),
    Endpoint("constructors", f"{PROBE_SEASON}/constructors", "§2 dim_constructor (SCD2)"),
    # Season-scoped rather than round-scoped on purpose. Nullability is measured
    # from what actually came back, so the sample has to contain the cases that
    # produce nulls. One race can have zero retirements — profile that and you
    # conclude `Time` is mandatory, then the first DNF breaks staging. A season
    # spans finishers, retirements and disqualifications.
    Endpoint("results", f"{PROBE_SEASON}/results", "§2 fct_results — the core fact"),
    Endpoint("qualifying", f"{PROBE_SEASON}/qualifying", "§3 grid vs finish"),
    Endpoint("sprint", f"{PROBE_SEASON}/{PROBE_SPRINT_ROUND}/sprint", "§1 sprint points affect standings"),
    Endpoint("pitstops", f"{PROBE_SEASON}/{PROBE_ROUND}/pitstops", "§6 reliability; high-cardinality — cap"),
    Endpoint("laps", f"{PROBE_SEASON}/{PROBE_ROUND}/laps", "high-cardinality — measure before committing"),
    Endpoint("driverstandings", f"{PROBE_SEASON}/{PROBE_ROUND}/driverstandings", "§1 fct_driver_standings"),
    Endpoint("constructorstandings", f"{PROBE_SEASON}/{PROBE_ROUND}/constructorstandings", "§1 fct_constructor_standings"),
    Endpoint("status", "status", "§6 retirement reasons; accepted_values / dim_status"),
)

# Headers worth keeping. Anything rate-limit shaped is captured by pattern
# because different gateways spell it differently.
HEADER_PATTERN = re.compile(
    r"(rate|limit|remaining|reset|retry|quota|throttl)", re.IGNORECASE
)

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


@dataclass
class Response:
    """One HTTP call and everything worth remembering about it."""

    url: str
    status: int | None
    latency_ms: float
    attempts: int
    payload: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.payload is not None


_call_count = 0


def budget_warning() -> str | None:
    """Warn when a run approaches the sustained hourly allowance.

    There are no rate-limit headers to read, so the only way to avoid tripping
    the 500/hour limit is to count locally. A probe that silently exceeds it
    gets the source's IP blocked, and the terms permit blocking without notice.
    """
    if _call_count > SUSTAINED_LIMIT_PER_HOUR:
        return (f"EXCEEDED the documented {SUSTAINED_LIMIT_PER_HOUR}/hour budget "
                f"({_call_count} calls) — pause for an hour before running again")
    if _call_count > SUSTAINED_LIMIT_PER_HOUR * 0.8:
        return (f"{_call_count} calls, approaching the {SUSTAINED_LIMIT_PER_HOUR}/hour "
                f"budget")
    return None


def fetch(session: requests.Session, url: str, params: dict[str, Any], delay: float) -> Response:
    """GET with exponential backoff on transient failures.

    Retry behaviour is built in here from the first line of code rather than
    added in a hardening pass, because that is the project's stated pattern
    (ARCHITECTURE §5) and because a discovery run that dies on a single 503
    tells you nothing. Non-transient statuses — a 404 from an endpoint that
    does not exist — are *findings*, not failures to retry.
    """
    global _call_count
    last_error: str | None = None
    started = time.perf_counter()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            _call_count += 1
            resp = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:  # timeout, DNS, connection reset
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == MAX_ATTEMPTS:
                break
            time.sleep(BACKOFF_BASE_SECONDS ** attempt)
            continue

        headers = {k: v for k, v in resp.headers.items() if HEADER_PATTERN.search(k)}

        if resp.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
            # Honour Retry-After when the server states it; guessing is rude
            # and gets you rate-limited harder.
            wait = float(resp.headers.get("Retry-After") or BACKOFF_BASE_SECONDS ** attempt)
            last_error = f"HTTP {resp.status_code}, retrying in {wait:.1f}s"
            time.sleep(wait)
            continue

        latency = (time.perf_counter() - started) * 1000
        time.sleep(delay)  # pacing, applied after every completed call

        if resp.status_code != 200:
            return Response(url, resp.status_code, latency, attempt, None, headers,
                            f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return Response(url, 200, latency, attempt, resp.json(), headers)
        except ValueError as exc:
            return Response(url, 200, latency, attempt, None, headers,
                            f"response was not JSON: {exc}")

    latency = (time.perf_counter() - started) * 1000
    return Response(url, None, latency, MAX_ATTEMPTS, None, {}, last_error)


# --------------------------------------------------------------------------
# Envelope discovery — find the records rather than assuming where they live
# --------------------------------------------------------------------------


def find_record_lists(payload: Any, prefix: str = "", found: dict[str, int] | None = None) -> dict[str, int]:
    """Map every path holding a list of objects to how many objects live there.

    Hardcoding `MRData.RaceTable.Races` would assume the very thing this
    script exists to verify. Walking the payload means an envelope change
    shows up as a changed report instead of a silent mis-parse.

    Counts are summed across *every* parent, not read off the first one. A
    `laps` response nests timings under laps under races: only the total tells
    you the endpoint returned 100 timing rows rather than 5 laps.
    """
    found = defaultdict(int) if found is None else found

    if isinstance(payload, dict):
        for key, value in payload.items():
            find_record_lists(value, f"{prefix}.{key}" if prefix else key, found)
    elif isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            found[prefix] += len(payload)
        for item in payload:
            find_record_lists(item, f"{prefix}[]", found)

    return dict(found)


def collect_records(payload: Any, path: str) -> list[dict]:
    """Gather every object at a dotted path, flattening across parents."""
    parts = path.split(".")

    def walk(node: Any, depth: int) -> list[dict]:
        if node is None:
            return []
        if depth == len(parts):
            return node if isinstance(node, list) else []

        part = parts[depth]
        if part.endswith("[]"):
            child = node.get(part[:-2]) if isinstance(node, dict) else None
            if not isinstance(child, list):
                return []
            gathered: list[dict] = []
            for element in child:
                gathered.extend(walk(element, depth + 1))
            return gathered

        return walk(node.get(part) if isinstance(node, dict) else None, depth + 1)

    return walk(payload, 0)


def pick_grain_path(record_lists: dict[str, int], total: int, limit: int) -> str | None:
    """Choose the list that carries the endpoint's actual grain.

    The envelope wraps records in containers — a `results` response is one race
    object holding twenty result objects — so the outermost list is almost
    never the grain. The row the API is really counting is the one whose
    population matches what pagination reports it returned, so match on that
    and break ties toward the shallower path.
    """
    if not record_lists:
        return None

    expected = min(total, limit) if total and limit else (total or limit)
    matches = [p for p, n in record_lists.items() if n == expected]
    if matches:
        return min(matches, key=lambda p: (p.count("[]"), p.count("."), len(p)))

    # Nothing matched (an empty or unpaginated response) — the deepest list is
    # the best remaining guess at the record, and it gets reported as a guess.
    return max(record_lists, key=lambda p: (p.count("[]"), p.count(".")))


# --------------------------------------------------------------------------
# Field profiling — nullability and types as observed, not as documented
# --------------------------------------------------------------------------

NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?Z?$")


def classify(value: Any) -> str:
    """Name the JSON type, and flag strings that are really something else.

    Ergast-lineage APIs return almost everything as a string — points, laps,
    positions. Knowing that up front is the difference between a staging layer
    that casts deliberately and one that discovers the problem in production.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, str):
        if value == "":
            return "empty-string"
        if NUMERIC_RE.match(value):
            return "string(numeric)"
        if DATE_RE.match(value):
            return "string(date)"
        if TIME_RE.match(value):
            return "string(time)"
        return "string"
    return type(value).__name__


def flatten(record: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten one record to (dotted_path, value) pairs, descending into lists."""
    pairs: list[tuple[str, Any]] = []

    if isinstance(record, dict):
        for key, value in record.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                pairs.append((path, value))  # record the container itself too
                pairs.extend(flatten(value, path))
            else:
                pairs.append((path, value))
    elif isinstance(record, list):
        for item in record:
            pairs.extend(flatten(item, f"{prefix}[]"))

    return pairs


def profile(records: list[dict]) -> dict[str, dict]:
    """Aggregate field statistics across every record returned."""
    occurrences: dict[str, int] = defaultdict(int)
    records_with: dict[str, set[int]] = defaultdict(set)
    nulls: dict[str, int] = defaultdict(int)
    types: dict[str, set[str]] = defaultdict(set)
    samples: dict[str, list] = defaultdict(list)
    distinct: dict[str, set[str]] = defaultdict(set)

    for index, record in enumerate(records):
        for path, value in flatten(record):
            occurrences[path] += 1
            records_with[path].add(index)
            kind = classify(value)
            types[path].add(kind)
            if kind in ("null", "empty-string"):
                nulls[path] += 1
            elif not isinstance(value, (dict, list)):
                if len(samples[path]) < 3:
                    samples[path].append(value)
                if len(distinct[path]) <= 30:
                    distinct[path].add(str(value))

    total = len(records) or 1
    out = {}
    for path in sorted(occurrences):
        present = len(records_with[path])
        card = len(distinct[path])
        out[path] = {
            "present_in_records": present,
            "present_pct": round(100 * present / total, 1),
            "occurrences": occurrences[path],
            "null_or_empty": nulls[path],
            "types": sorted(types[path]),
            "samples": samples[path],
            # A low distinct count on a categorical is the signal for an
            # accepted_values test (SECURITY §4, tier 2).
            "distinct_values": sorted(distinct[path]) if card <= 30 else None,
        }
    return out


# --------------------------------------------------------------------------
# Per-endpoint probe
# --------------------------------------------------------------------------


def probe(session: requests.Session, base_url: str, endpoint: Endpoint, delay: float) -> dict:
    """Run every question against one endpoint and return the findings."""
    url = f"{base_url}/{endpoint.path}.json"
    result: dict[str, Any] = {
        "name": endpoint.name,
        "serves": endpoint.serves,
        "url": url,
    }

    first = fetch(session, url, {"limit": PROFILE_LIMIT, "offset": 0}, delay)
    result["status"] = first.status
    result["latency_ms"] = round(first.latency_ms, 1)
    result["attempts"] = first.attempts
    result["rate_limit_headers"] = first.headers

    if not first.ok:
        # A 404 here is a real finding — the endpoint does not exist on this
        # source and whatever it was going to feed needs another plan.
        result["error"] = first.error
        return result

    payload = first.payload
    envelope = {k: v for k, v in payload.items() if not isinstance(v, (dict, list))}
    result["envelope_scalars"] = envelope
    result["top_level_keys"] = sorted(payload.keys())

    record_lists = find_record_lists(payload)
    result["record_lists"] = record_lists

    # Pagination metadata lives in the envelope on Ergast-lineage APIs, but
    # confirm it rather than trusting it. Note these arrive as strings.
    inner = payload.get("MRData", payload)
    pagination = {k: inner[k] for k in ("total", "limit", "offset")
                  if isinstance(inner, dict) and k in inner} if isinstance(inner, dict) else {}
    result["pagination_metadata"] = pagination
    result["pagination_types"] = {k: classify(v) for k, v in pagination.items()}

    def as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    total, limit = as_int(pagination.get("total")), as_int(pagination.get("limit"))

    if not record_lists:
        # A 200 with no records is an *empty result set*, not a broken
        # endpoint. The difference decides whether this feeds a model.
        result["empty_result_set"] = True
        result["error"] = f"200 but no records (reported total={pagination.get('total')})"
        return result

    grain_path = pick_grain_path(record_lists, total, limit)
    result["grain_record_path"] = grain_path
    result["envelope_record_path"] = min(record_lists, key=lambda p: (p.count("[]"), len(p)))
    result["grain_path_matched_total"] = record_lists.get(grain_path) == min(total, limit)

    records = collect_records(payload, grain_path) if grain_path else []
    result["rows_returned"] = len(records)
    result["sample_record"] = records[0] if records else None
    result["field_profile"] = profile(records)

    # What is the real per-call cap? Ask for more than anyone would grant, then
    # check whether the server errors or silently clamps — a silent clamp that
    # goes unnoticed produces a backfill that quietly stops short.
    cap = fetch(session, url, {"limit": CAP_PROBE_LIMIT, "offset": 0}, delay)
    if cap.ok:
        cap_inner = cap.payload.get("MRData", cap.payload)
        cap_records = collect_records(cap.payload, grain_path) if grain_path else []
        reported_limit = cap_inner.get("limit") if isinstance(cap_inner, dict) else None
        result["row_cap_probe"] = {
            "requested": CAP_PROBE_LIMIT,
            "rows_returned": len(cap_records),
            "reported_limit": reported_limit,
            "reported_total": cap_inner.get("total") if isinstance(cap_inner, dict) else None,
            "silently_clamped": as_int(reported_limit) < CAP_PROBE_LIMIT,
        }
    else:
        result["row_cap_probe"] = {"error": cap.error}

    # Does offset actually move the window? If it silently does not, every
    # backfill built on it would quietly return page one forever.
    if len(records) >= 2:
        page2 = fetch(session, url, {"limit": 1, "offset": 1}, delay)
        if page2.ok:
            page2_records = collect_records(page2.payload, grain_path) if grain_path else []
            moved = bool(page2_records) and page2_records[0] != records[0]
            result["offset_probe"] = {
                "works": moved,
                "matches_second_record": bool(page2_records) and page2_records[0] == records[1],
            }
        else:
            result["offset_probe"] = {"error": page2.error}
    else:
        result["offset_probe"] = {"skipped": "fewer than 2 records to compare"}

    return result


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def casting_flags(field_profile: dict) -> list[str]:
    """Fields whose JSON type is not the type they should be stored as."""
    flags = []
    for path, stats in field_profile.items():
        kinds = set(stats["types"])
        if kinds & {"string(numeric)", "string(date)", "string(time)"}:
            flags.append(f"{path} ({', '.join(sorted(kinds - {'null'}))})")
    return flags


def nullable_fields(field_profile: dict) -> list[str]:
    """Fields that are absent or null in practice — never eligible for a key."""
    return [
        f"{path} (present in {stats['present_pct']}%, {stats['null_or_empty']} null/empty)"
        for path, stats in field_profile.items()
        if stats["present_pct"] < 100 or stats["null_or_empty"] > 0
    ]


def write_markdown(results: list[dict], base_url: str, path: Path) -> None:
    """Emit a summary shaped like SCHEMA.md's Phase 0 table."""
    lines = [
        "# Phase 0 — source discovery findings",
        "",
        "> Generated by `discovery/probe_source.py`. Every value here was observed",
        "> from a live response, not taken from documentation.",
        ">",
        "> Contains excerpts of data from **Jolpica-F1**, licensed under",
        "> [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).",
        "",
        f"- **Source:** `{base_url}`",
        f"- **Probed at:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- **Probe season / round:** {PROBE_SEASON} / {PROBE_ROUND}",
        "",
        "## Endpoint inventory",
        "",
        "| Endpoint | Status | Grain record path | Rows | Reported total | Max per call | Offset works |",
        "|---|---|---|---|---|---|---|",
    ]

    for r in results:
        if r.get("status") != 200:
            lines.append(
                f"| `{r['name']}` | ❌ {r.get('status') or 'error'} | — | — | — | — | — |"
            )
            continue
        if r.get("empty_result_set"):
            lines.append(
                f"| `{r['name']}` | ⚠️ 200 empty | — | 0 | "
                f"{r.get('pagination_metadata', {}).get('total', '—')} | — | — |"
            )
            continue
        cap = r.get("row_cap_probe", {})
        offset = r.get("offset_probe", {})
        pagination = r.get("pagination_metadata", {})
        lines.append(
            f"| `{r['name']}` | ✅ 200 | `{r.get('grain_record_path', '—')}` "
            f"| {r.get('rows_returned', '—')} | {pagination.get('total', '—')} "
            f"| {cap.get('reported_limit', '—')} | {'✅' if offset.get('works') else '—'} |"
        )

    # Rate limits are a Phase 0 checklist item. If the server sends no
    # rate-limit headers, that absence is the finding — the budget has to come
    # from the published policy and be respected by pacing, because the
    # pipeline cannot read its remaining allowance at runtime.
    observed_headers = {k: v for r in results for k, v in r.get("rate_limit_headers", {}).items()}
    lines += ["", "## Rate limiting", ""]
    if observed_headers:
        lines += ["Headers observed:", ""]
        lines += [f"- `{k}: {v}`" for k, v in sorted(observed_headers.items())]
    else:
        lines += [
            "**No rate-limit headers returned on any endpoint.** The pipeline cannot",
            "read its remaining allowance at runtime, so the budget must be taken from",
            "the source's published policy and enforced by deliberate client-side",
            "pacing plus `Retry-After` handling on 429.",
        ]
    lines += [""]

    lines += ["", "## Why each endpoint is probed", "",
              "| Endpoint | Serves |", "|---|---|"]
    for r in results:
        lines.append(f"| `{r['name']}` | {r['serves']} |")

    lines += ["", "## Per-endpoint detail", ""]
    for r in results:
        lines += [f"### `{r['name']}`", ""]
        if r.get("status") != 200 or r.get("empty_result_set"):
            lines += [f"**{r.get('error', 'unknown')}**", ""]
            continue

        lines += [
            f"- URL: `{r['url']}`",
            f"- Latency: {r['latency_ms']} ms (attempts: {r['attempts']})",
            f"- Envelope container: `{r.get('envelope_record_path', '—')}`",
            f"- Grain record path: `{r.get('grain_record_path', '—')}`"
            + ("" if r.get("grain_path_matched_total") else " _(inferred — did not match reported total)_"),
            f"- Nested lists found: " + ", ".join(
                f"`{p}` ({n})" for p, n in r.get("record_lists", {}).items()
            ),
            f"- Pagination field types: " + ", ".join(
                f"`{k}` → {v}" for k, v in r.get("pagination_types", {}).items()
            ),
            "",
        ]

        casts = casting_flags(r.get("field_profile", {}))
        if casts:
            lines += ["**Needs casting at staging:**", ""]
            lines += [f"- `{c}`" for c in casts]
            lines += [""]

        nulls = nullable_fields(r.get("field_profile", {}))
        if nulls:
            lines += ["**Nullable in practice — not eligible for a key:**", ""]
            lines += [f"- `{n}`" for n in nulls]
            lines += [""]
        else:
            lines += ["**No nullable fields observed in this sample.**", ""]

    lines += [
        "## Still to do (not answered by this script)",
        "",
        "- [ ] Join coverage: what share of fact FKs exist in the dimension source",
        "- [ ] Source classification: immutable event / mutable reference / snapshot",
        "- [ ] Licence and attribution terms confirmed",
        "- [ ] Storage and warehouse connectivity smoke test",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


# Entities whose all-time row count decides the backfill budget. Measured by
# asking for one row and reading the reported total — one cheap call per
# entity, rather than estimating and being wrong by an order of magnitude.
VOLUME_PATHS: tuple[tuple[str, str], ...] = (
    ("seasons", "seasons"),
    ("circuits", "circuits"),
    ("races", "races"),
    ("drivers", "drivers"),
    ("constructors", "constructors"),
    ("results", "results"),
    ("qualifying", "qualifying"),
    ("sprint", "sprint"),
    ("pitstops", "pitstops"),
    ("laps", "laps"),
    ("status", "status"),
    ("driverstandings (one season)", f"{PROBE_SEASON}/driverstandings"),
    ("results (one season)", f"{PROBE_SEASON}/results"),
)


def measure_volumes(session: requests.Session, base_url: str, delay: float, page_size: int = 100) -> list[dict]:
    """Report all-time row counts and the calls a full backfill would cost."""
    out = []
    for label, path in VOLUME_PATHS:
        resp = fetch(session, f"{base_url}/{path}.json", {"limit": 1, "offset": 0}, delay)
        row: dict[str, Any] = {"entity": label, "path": path}
        if resp.ok:
            inner = resp.payload.get("MRData", resp.payload)
            try:
                total = int(inner.get("total", 0))
            except (TypeError, ValueError):
                total = 0
            row["total_rows"] = total
            row["calls_at_100"] = -(-total // page_size)  # ceiling division
        else:
            row["error"] = resp.error
        out.append(row)
        print(f"  {label:30s} {row.get('total_rows', row.get('error', ''))}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--only", nargs="*", help="probe only these endpoint names")
    parser.add_argument("--out", default="discovery/findings")
    parser.add_argument("--volumes", action="store_true",
                        help="measure all-time row counts and backfill call cost")
    args = parser.parse_args()

    selected = [e for e in ENDPOINTS if not args.only or e.name in args.only]
    if not selected:
        print(f"No endpoints matched {args.only}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    if args.volumes:
        print(f"Measuring all-time volumes at {args.base_url}\n")
        volumes = measure_volumes(session, args.base_url, args.delay)
        volume_path = out_dir / "volumes.json"
        volume_path.write_text(json.dumps(volumes, indent=2), encoding="utf-8")
        print(f"\n  {volume_path}")
        return 0

    print(f"Probing {args.base_url} — {len(selected)} endpoints\n")
    results = []
    for endpoint in selected:
        print(f"  {endpoint.name:24s} ", end="", flush=True)
        # One endpoint failing must not end the run (ARCHITECTURE §5).
        try:
            result = probe(session, args.base_url, endpoint, args.delay)
        except Exception as exc:  # noqa: BLE001 - discovery must survive anything
            result = {"name": endpoint.name, "serves": endpoint.serves,
                      "status": None, "error": f"{type(exc).__name__}: {exc}"}
        results.append(result)

        if result.get("status") == 200 and not result.get("empty_result_set"):
            print(f"200  {result.get('rows_returned', 0):>5} rows  "
                  f"{result.get('grain_record_path', '')}")
        elif result.get("empty_result_set"):
            print("200  empty result set")
        else:
            print(f"FAIL {str(result.get('error', ''))[:70]}")

    json_path = out_dir / "source_probe.json"

    # Merge into any existing report rather than replacing it. Re-probing one
    # endpoint with --only must not silently discard the findings for the
    # other twelve; partial evidence that looks complete is worse than none.
    merged: dict[str, dict] = {}
    if json_path.exists():
        try:
            for previous in json.loads(json_path.read_text(encoding="utf-8")):
                merged[previous["name"]] = previous
        except (ValueError, KeyError, TypeError):
            print("  (existing report unreadable — writing a fresh one)")
    merged.update({r["name"]: r for r in results})

    order = {e.name: i for i, e in enumerate(ENDPOINTS)}
    report = sorted(merged.values(), key=lambda r: order.get(r["name"], len(order)))

    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path = out_dir / "source_probe.md"
    write_markdown(report, args.base_url, md_path)

    ok = sum(1 for r in results if r.get("status") == 200)
    print(f"\n{ok}/{len(results)} endpoints returned 200  ({_call_count} API calls)")
    if warning := budget_warning():
        print(f"  ! {warning}")
    print(f"  {json_path}\n  {md_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
