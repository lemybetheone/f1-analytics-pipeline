# Schema & Data Model — F1 Analytics Pipeline

> Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Defines the physical schema
> across all layers, the dimensional model, and the grain of every table.
>
> **Status:** the framework (the Phase 0 gate, the design order, the layer
> contract, the testing tiers) carries over from a previous project unchanged.
> The dimensional model in §4 is a **candidate**, derived backwards from
> [PRD §6](PRD.md#6-analytical-questions-the-model-must-answer) but not yet
> validated against real payloads. The raw (§2) and staging (§3) layers remain
> `TODO` until F1 Phase 0 completes — never write them from assumptions.

---

## Phase 0 gate — complete before designing any table

> Schemas written from assumptions rather than observed payloads are the most
> common source of rework. Container types, date representations, nullability,
> and row caps are routinely different from what documentation implies.
> **Validate first, then design.**

**Source: Jolpica-F1** (`https://api.jolpi.ca/ergast/f1`), confirmed live
2026-08-02 — all 13 endpoints returned HTTP 200. Evidence:
[`discovery/findings/source_probe.md`](../discovery/findings/source_probe.md),
reproducible with `python discovery/probe_source.py`.

Every value below was **observed from a live response**, not read from
documentation. All record paths are nested under an `MRData` envelope.

| Endpoint | Records live at | Rows/call (cap 100) | All-time rows | Key fields | Needs casting |
|---|---|---|---|---|---|
| `seasons` | `SeasonTable.Seasons` | 77 | 77 | `season` | `season` → int |
| `circuits` | `CircuitTable.Circuits` | 78 | 78 | `circuitId` | `lat`, `long` → numeric |
| `races` | `RaceTable.Races` | 24/season | 1,172 | `season`, `round` | `date`, `time`, 18 session fields |
| `drivers` | `DriverTable.Drivers` | 25/season | 881 | `driverId` | `dateOfBirth`, `permanentNumber` |
| `constructors` | `ConstructorTable.Constructors` | 10/season | 214 | `constructorId` | none |
| `results` | `RaceTable.Races[].Results` | 100 | 26,115 | `season`, `round`, `Driver.driverId` | 13 fields incl. `points`, `grid`, `position`, `laps`, `millis` |
| `qualifying` | `RaceTable.Races[].QualifyingResults` | 100 | 11,212 | `season`, `round`, `Driver.driverId` | `position`, `number` |
| `sprint` | `RaceTable.Races[].SprintResults` | 20/race | 568 | `season`, `round`, `Driver.driverId` | 11 fields |
| `pitstops` | `RaceTable.Races[].PitStops` | 43/race | **requires season+round** | `season`, `round`, `driverId`, `stop` | `lap`, `stop`, `duration`, `time` |
| `laps` | `RaceTable.Races[].Laps[].Timings` | 100 | **1,129 per race** | `season`, `round`, `lap`, `driverId` | `position` |
| `driverstandings` | `StandingsTable.StandingsLists[].DriverStandings` | 20–24/round | **per-round only** | `season`, `round`, `Driver.driverId` | `points`, `position`, `wins` |
| `constructorstandings` | `StandingsTable.StandingsLists[].ConstructorStandings` | 10/round | **per-round only** | `season`, `round`, `Constructor.constructorId` | `points`, `position`, `wins` |
| `status` | `StatusTable.Status` | 100 | 136 | `statusId` | `count`, `statusId` |

### Findings that change the design

1. **Every scalar is a string.** `points: "26"`, `grid: "1"`, `millis: "5504742"`
   — and so is the pagination metadata (`total: "479"`). Staging casts
   everything; nothing may be trusted as-typed.
2. **The per-call cap is 100 and the server clamps silently.** `limit=2000`
   returns HTTP 200 with `"limit": "100"`. A backfill assuming a larger page
   stops short and reports success.
3. **Nullable in practice** (from a full-season sample, not one race):
   `Time` 90% — absent for every DNF · `FastestLap` 97% · `Q2` 76% · `Q3` 51%
   · on `races`, the `Sprint`/`SprintQualifying`/`SecondPractice` session
   blocks are absent on non-sprint weekends. **None of these may appear in a
   key, and none should carry a `not_null` test.**
4. **`positionText` is the DNF marker,** carrying `R` alongside numeric
   positions; `position` alone cannot distinguish a retirement.
5. **`driverstandings.Constructors` is a list** — a driver who changes team
   mid-season has several. Confirms `constructor_key` belongs on `fct_results`
   at race grain, not on the standings fact.
6. **`pitstops` and `laps` reject season-scoped calls** (HTTP 400: requires
   `season_year` + `race_round`), so they cost at least one call per race.
7. **Per-round standings require one call per race.** Season-scoped
   `/{season}/driverstandings` returns only the *final* round; `limit`/`offset`
   page the driver rows inside that single list, not across rounds.
8. **Join coverage is 100%** across all 26,115 result rows, 1950→2024:
   every `driverId`, `constructorId`, `status` and `(season, round)` resolves,
   and all 1,172 races resolve to a circuit. Evidence:
   [`discovery/findings/join_coverage.md`](../discovery/findings/join_coverage.md).
9. **Field availability is era-dependent, and a modern sample lies about it.**
   Measured across every decade:

   | Field | 1950s–1990s | 2000s | 2010s+ |
   |---|---|---|---|
   | `FastestLap` | **absent entirely** | 58% | 96% |
   | `Time` | 16–23% | 38% | 47–74% |
   | `grid`, `laps`, `number`, `points`, `position`, `positionText` | 100% | 100% | 100% |

   A 2024-only sample reported `FastestLap` at 97% and `Time` at 90%. Over
   full history they are 0% and ~17% in the early decades. **Only the six
   always-present fields may carry `not_null` tests**; a `not_null` on
   `FastestLap` would pass on recent data and fail the moment the backfill
   reaches 1999.

**Checklist:**
- [x] Chose and confirmed the live source API — Jolpica-F1, 13/13 endpoints 200
- [ ] Confirmed its **terms of use / licence** — still outstanding
- [x] Called every endpoint and inspected a real payload
- [x] Confirmed container type (records are nested lists under `MRData`)
- [x] Confirmed date/time representation — ISO date strings + separate time strings, never epochs
- [x] Confirmed per-call row caps (100, silently clamped) and pagination (`limit`/`offset`, verified working)
- [~] Confirmed rate limits — **no rate-limit headers are returned on any
      endpoint**; the published policy still needs confirming, and pacing must
      be client-side
- [x] Confirmed which fields are **nullable in practice**, not just in docs
- [x] Measured **join coverage** — 100% on all four `fct_results` edges across
      26,115 rows and all 1,172 race→circuit edges
- [x] Classified each source: immutable event / mutable reference / snapshot —
      see §2. No source requires an append-only `snapshot_date`
- [x] Verified connectivity to object storage and the warehouse — both PASS
      2026-08-03, schemas created

## Design order

1. Analytical questions
   ([PRD §6](PRD.md#6-analytical-questions-the-model-must-answer))
2. → Star schema (dimensions, facts, grain)
3. → Staging models needed to feed them
4. → **Raw schema last**, designed to supply the above

> **Rule:** never design the raw layer first. A raw layer designed in isolation
> produces tables that cannot answer the questions the project exists to answer.

---

## 1. Layer overview

| Layer | Schema | Materialisation | Grain |
|---|---|---|---|
| Raw | `raw` | tables | As landed from source |
| Staging | `staging` | views | 1:1 with raw source |
| Marts | `marts` | tables | Declared per dimension/fact |

## 2. Raw layer

Classified 2026-08-03 from observed data. Evidence:
[`discovery/findings/mutability.json`](../discovery/findings/mutability.json).

| Table | Grain | Load pattern | Why |
|---|---|---|---|
| `raw.seasons` | `season` | Upsert | Reference; two columns, correctable, no history value |
| `raw.circuits` | `circuit_id` | Upsert | Reference; names and coordinates are correctable upstream |
| `raw.races` | `(season, round)` | Upsert | **Mutable until run, immutable after.** Session dates shift during a live season, so insert-do-nothing would freeze a stale schedule |
| `raw.drivers` | `driver_id` | Upsert | Reference; biographical fields, corrections possible, history not needed |
| `raw.constructors` | `constructor_id` | Upsert | Reference. **No id was ever observed carrying two names** across 1996–2024 — rebrands are separate ids |
| `raw.results` | `(season, round, driver_id)` | Upsert | **Not purely immutable.** The race is over, but penalties, appeals and disqualifications amend published results days later. Insert-do-nothing would silently keep the pre-penalty record |
| `raw.qualifying` | `(season, round, driver_id)` | Upsert | Same correction window as results |
| `raw.sprint` | `(season, round, driver_id)` | Upsert | Same correction window as results |
| `raw.pitstops` | `(season, round, driver_id, stop)` | Insert `ON CONFLICT DO NOTHING` | Genuinely immutable: timing measurements, not adjudicated outcomes |
| `raw.driver_standings` | `(season, round, driver_id)` | Upsert | Periodic snapshot whose snapshot key is `round`. Corrections propagate when a result is amended |
| `raw.constructor_standings` | `(season, round, constructor_id)` | Upsert | As above |
| `raw.status` | `status_id` | Upsert | Small reference code list |
| `raw.failed_ingestions` | append log | Insert | Dead-letter for retry |

**The classification finding that matters:** no source here requires an
append-only `snapshot_date`. History that matters is already carried in a
natural key — standings by `round`, a driver's constructor by `(season, round)`
on the results fact. The source is a historical record that expresses change by
issuing new rows and new ids, not by mutating old ones.

**The one correction-window subtlety:** `results`, `qualifying`, `sprint` and
both standings tables look like immutable events but are not. F1 results are
adjudicated, and a stewards' decision can change a finishing position or a
points total after publication. Upsert keeps the warehouse aligned with the
record books; the raw JSON in the lake preserves what was originally returned,
so the amendment is still traceable.

**Rules**
- Every table carries `ingested_at`.
- Snapshot tables carry `snapshot_date`; the key is `(natural_key, snapshot_date)`.
  _No table in §2 meets this: standings snapshot on `round`, which is already
  part of the natural key._
- No nullable column may appear in a primary key.
- Raw stores what the source returned — casting and renaming happen in staging.

## 3. Staging layer

One model per raw table, named `stg_<source>`, materialised as views.

**Responsibilities:** rename vague columns · cast types · convert epochs/ISO
strings to UTC timestamps (keeping the raw value) · declare and test grain.

**Not allowed:** joins, aggregation, business logic, filtering rows.

> `TODO (Phase 0/2):` list `stg_<source>` models and their grains.

## 4. Dimensional model (marts)

> **Candidate model, derived backwards from [PRD §6](PRD.md#6-analytical-questions-the-model-must-answer).**
> Confirm every grain, key, type, and nullability against **real Phase 0
> payloads** before building — column availability (e.g. does the source expose
> `grid` separately from qualifying position? a machine-readable `status`?) is
> assumed here, not verified.

### Facts

| Model | Grain | Type | Feeds §6 |
|---|---|---|---|
| `fct_results` | one row per **(race, driver)** | Transaction | Themes 2, 3, 4, 6 — the core fact |
| `fct_driver_standings` | one row per **(season, round, driver)** | Periodic snapshot | Theme 1 |
| `fct_constructor_standings` | one row per **(season, round, constructor)** | Periodic snapshot | Theme 1 |
| `fct_qualifying` _(optional)_ | one row per **(race, driver)** | Transaction | Theme 3, only if Q1/Q2/Q3 times or quali≠grid needed |

> `fct_results` carries: `driver_key`, `constructor_key`, `race_key`,
> `grid_position`, `finish_position`, `points`, `status`, and finish/DNF flags.
> Both `driver_key` **and** `constructor_key` live here so teammate head-to-head
> (Theme 4) is a self-join, not a new table.

### Dimensions

| Model | Grain | SCD | Notes |
|---|---|---|---|
| `dim_driver` | one row per driver | Type 1 _(confirm)_ | Name, nationality, DOB. Season team lives on the fact, not here |
| `dim_constructor` | one row per constructor | **Type 1** _(was Type 2; reversed 2026-08-03)_ | No `constructorId` was ever observed carrying two names across 1996–2024 — rebrands are separate ids upstream, so there is no attribute for SCD2 to track |
| `dim_race` | one row per **(season, round)** | Type 1 | circuit, season, round, date; the event/date dimension |
| `dim_circuit` | one row per circuit | Type 1 | For circuit-level rollups (Theme 5) |
| `dim_status` _(optional)_ | one row per status value | Type 1 | Groups retirement reasons (mechanical / collision / finished) for Theme 6 |

> **Decisions, see [PRD §6](PRD.md#modelling-notes-open-decisions):** standings
> split into two entity facts; **ingest official standings + reconcile** a
> derived cumulative total as a DQ test (both locked 2026-08-01). **SCD2 on
> `dim_constructor` was reversed 2026-08-03** on Phase 0 evidence — every
> dimension here is Type 1.

## 5. Key patterns to implement

**Surrogate keys** — every dimension gets a hashed surrogate key
(`dbt_utils.generate_surrogate_key`) rather than exposing source ids as the join
key. Decouples the warehouse from source-system quirks.

**Unknown member** — dimensions include a synthetic "Unknown" row, and facts map
unmatched foreign keys to it. **Measured in Phase 0: coverage is 100%**, so the
Unknown member is insurance against future source drift rather than a
load-bearing part of the model today, and `relationships` tests can be strict on
every fact-to-dimension edge. It is still built: a source that adds an unmatched
key later should route the row to Unknown, not lose it to an inner join.

**SCD Type 2** — **not used in this project.** Phase 0 classification (§2) found
no source that changes an attribute in place: history is carried in natural keys
(standings by `round`, a driver's constructor by `(season, round)` on the
results fact), and constructor rebrands are separate ids upstream rather than a
mutated name. Building `valid_from`/`valid_to`/`is_current` with no varying data
behind it would be structure for its own sake. Revisit only if a source is added
that genuinely mutates.

**Range / temporal joins** — **not needed**, for the same reason: with no
time-bounded dimension rows, every fact-to-dimension edge is a direct key join.

**Incremental facts** — large fact tables process only new rows per run.

## 6. Testing per layer

| Layer | Minimum tests |
|---|---|
| Sources | Key not-null + unique; freshness |
| Staging | Grain (unique / composite-unique) + not-null on join keys |
| Dimensions | Surrogate key unique + not-null; accepted values on categoricals |
| Facts | Grain; `relationships` to every dimension; not-null on FKs |

**Test deliberately.** Tests exist to protect assumptions that downstream logic
depends on. Testing descriptive fields nobody joins or filters on creates alert
fatigue and erodes trust in the suite.

## 7. Data dictionary

_Maintain per mart model — column, type, description, source lineage. Generate
and publish `dbt docs` rather than hand-maintaining this section._
