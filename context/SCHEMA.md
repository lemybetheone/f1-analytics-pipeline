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

For **every** endpoint, record the observed reality before creating tables. The
rows below are *candidate* F1 entities — replace them with the real endpoints of
the source chosen in Phase 0 (see [PRD §1](PRD.md#1-problem-statement)):

| Endpoint | Returns (list/dict) | Rows per call | Pagination | Key fields | Types that need casting |
|---|---|---|---|---|---|
| seasons | | | | | |
| circuits | | | | | |
| races / schedule | | | | | |
| drivers | | | | | |
| constructors | | | | | |
| results | | | | | |
| qualifying | | | | | |
| pit stops / laps | | | | | |
| standings (driver + constructor) | | | | | |

**Checklist:**
- [ ] Chose and confirmed the live source API + its terms of use
- [ ] Called every endpoint and inspected a real payload
- [ ] Confirmed container type (a list is not a dict)
- [ ] Confirmed date/time representation (epoch int vs ISO string) → drives column type
- [ ] Confirmed per-call row caps and the pagination mechanism
- [ ] Confirmed rate limits (per second / minute / day)
- [ ] Confirmed which fields are **nullable in practice**, not just in docs
- [ ] Measured **join coverage** — what share of fact foreign keys actually exist
      in the dimension source (drives the Unknown-member design)
- [ ] Classified each source: immutable event / mutable reference / **snapshot requiring history**
- [ ] Verified connectivity to object storage and the warehouse

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

> `TODO (Phase 0):` one row per source endpoint. For each, record **grain**,
> **load pattern** (immutable event insert-do-nothing / mutable reference upsert /
> append-only snapshot), and the reason. Classify from observed data, not docs.

| Table | Grain | Load pattern | Notes |
|---|---|---|---|
| `raw.<entity>` | `TODO` | `TODO` | `TODO` |
| `raw.failed_ingestions` | append log | Insert | Dead-letter for retry |

**Rules**
- Every table carries `ingested_at`.
- Snapshot tables carry `snapshot_date`; the key is `(natural_key, snapshot_date)`.
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
| `dim_constructor` | one row per constructor **per validity span** | **Type 2 (decided)** | SCD2 on rebrands (Toro Rosso→AlphaTauri→RB); `valid_from`/`valid_to`/`is_current`. Facts join the row current as of the race date |
| `dim_race` | one row per **(season, round)** | Type 1 | circuit, season, round, date; the event/date dimension |
| `dim_circuit` | one row per circuit | Type 1 | For circuit-level rollups (Theme 5) |
| `dim_status` _(optional)_ | one row per status value | Type 1 | Groups retirement reasons (mechanical / collision / finished) for Theme 6 |

> **Decisions locked (2026-08-01), see [PRD §6](PRD.md#modelling-notes-open-decisions):**
> standings split into two entity facts; **ingest official standings + reconcile**
> a derived cumulative total as a DQ test; **SCD2 on `dim_constructor`** for
> rebrands (Type 2), `dim_driver` stays Type 1.

## 5. Key patterns to implement

**Surrogate keys** — every dimension gets a hashed surrogate key
(`dbt_utils.generate_surrogate_key`) rather than exposing source ids as the join
key. Decouples the warehouse from source-system quirks.

**Unknown member** — dimensions include a synthetic "Unknown" row, and facts map
unmatched foreign keys to it. Measure the actual join coverage in Phase 0 and
design for the gap rather than assuming full coverage.

**SCD Type 2** — built on append-only snapshots: compare consecutive
`snapshot_date` rows and emit `valid_from` / `valid_to` / `is_current`. Use for
attributes that change over time and whose history matters (e.g. a driver's
constructor across a career — confirm during Phase 0 which attributes need this).

**Range / temporal joins** — where an event carries no direct key to a
time-bounded reference, attach it by joining on a time range (event timestamp
between the reference's valid-from and the next one's). Identify any such case in
Phase 0.

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
