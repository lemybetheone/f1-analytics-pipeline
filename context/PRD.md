# PRD — F1 Analytics Pipeline

> **Purpose of this document:** lock the scope before building. If a task
> doesn't serve a goal or success metric below, it goes to the
> [Parking Lot](#parking-lot) — not into the build.
>
> **Status of this file:** the framework carries over from a previous project.
> Source selection, functional requirements (§7) and volume targets (§8a) are
> now validated against the live API; the dashboard spec (§8b) is still a
> placeholder.

| | |
|---|---|
| **Owner** | Lemuel Calinog |
| **Status** | Phase 0 — discovery in progress (source validated) |
| **Last updated** | 2026-08-02 |
| **Target completion** | _(set a date)_ |

---

## 1. Problem statement

Formula 1 produces a rich, well-structured public record — race results,
qualifying, lap times, pit stops, driver and constructor standings, and (for
recent seasons) car telemetry. It is freely available but raw and unmodelled,
so questions that span sessions, circuits, and seasons are not directly
queryable for analysis.

> **Source selected: Jolpica-F1** (`api.jolpi.ca/ergast/f1`), validated
> 2026-08-02 — all 13 endpoints returned HTTP 200 and every payload was
> inspected. See [SCHEMA § Phase 0](SCHEMA.md#phase-0-gate--complete-before-designing-any-table)
> and [ARCHITECTURE § Decision log](ARCHITECTURE.md#7-decision-log-adr-lite) row 8.
> The candidates assessed:
> - **Jolpica-F1** (`api.jolpi.ca`) — community successor to the Ergast API;
>   results, standings, schedules, laps, pit stops, 1950→present.
> - **OpenF1** (`openf1.org`) — telemetry, car data, positions, intervals, team
>   radio; recent seasons (≈2023+).
> - **FastF1** (Python library) — convenience layer over F1 live timing + Ergast
>   with caching; a library, not a REST API.
> - **Note:** the original **Ergast API was deprecated (shut down end of 2024)** —
>   do not build against it. Confirm the real, current endpoint and its terms in
>   Phase 0 before designing any table.

## 2. Why this project exists

This is a **portfolio project** demonstrating end-to-end data engineering for
Analytics Engineer / Data Engineer applications. Every scope decision is judged
against that goal, not against building the most feature-complete F1 product.

**Primary goal:** a complete, well-modelled, tested, documented pipeline that
the author can **explain and defend in an interview**.

**Secondary goal:** demonstrate the open-source modern data stack
(Python / object storage / dbt / Airflow) alongside cloud-warehouse experience.

> **Guiding principle:** a finished, explainable pipeline beats a sprawling,
> half-built one. Depth of understanding matters more than feature count.

## 3. Goals & non-goals

**Goals**
- G1 — Ingest F1 data reliably and idempotently from a public API.
- G2 — Model it into a tested star schema (dimensions + facts).
- G3 — Preserve history so slowly-changing attributes and trends are analysable.
- G4 — Orchestrate it on a schedule, with failure handling.
- G5 — Serve it through a dashboard answering real questions.
- G6 — Document it so a reviewer understands the architecture in 90 seconds.

**Non-goals (explicitly out of scope)**
- ❌ Real-time / live-timing streaming ingestion (batch only).
- ❌ Complete telemetry backfill for all sessions and seasons.
- ❌ A public-facing product, API, or user accounts.
- ❌ ML / predictive modelling (lap-time or race-outcome prediction).
- ❌ Multi-cloud or cloud-agnostic abstraction.

## 4. Users

| User | Need |
|---|---|
| **Hiring manager / reviewer** (primary) | Understand the architecture and judge engineering quality fast |
| **Analyst persona** (design driver) | Answer driver / constructor / circuit performance questions via dashboard |

## 5. MVP definition

The MVP is **done** when all of the following are true. Anything beyond this is
enhancement, not MVP.

- [ ] Ingestion runs end-to-end from a single command, idempotently.
- [ ] Raw data lands in object storage, partitioned by date.
- [ ] Warehouse loads from the lake, not from memory.
- [ ] Staging models exist for every source, each with a tested grain.
- [ ] A star schema with ≥3 dimensions and ≥2 fact tables.
- [ ] Data quality tests pass on every model (grain + key integrity minimum).
- [ ] One orchestrated scheduled run (DAG) that succeeds end to end.
- [ ] A dashboard answering ≥3 real analytical questions.
- [ ] README with architecture diagram and a "run it in 3 commands" section.

## 6. Analytical questions the model must answer

> The star schema is designed **backwards from these**. If a question cannot be
> answered by the model, the model is wrong — not the question. The `→` note
> under each theme records the fact/dimension it implies; the concrete model is
> declared in [SCHEMA §4](SCHEMA.md#4-dimensional-model-marts) and confirmed
> against real payloads in Phase 0.

**1. Standings & championship progress**
- Driver standings after each round of a season?
- Constructor standings after each round of a season?
- How did the points gap between the top 2–3 drivers evolve race-by-race?
- At what point in the season was the title effectively decided?

→ Periodic-snapshot fact at grain **(season, round, entity)**. Split into
`fct_driver_standings` and `fct_constructor_standings` — see [§ modelling notes](#modelling-notes-open-decisions).

**2. Race results & performance** _(the core fact)_
- Finishing order for a given race?
- Points scored by a driver/constructor in a race?
- A driver's win rate / podium rate / points-per-race over a season or career?
- Which constructor scored the most points in a season?
- Races finished vs. DNF, and why (status)?

→ Transaction fact `fct_results` at grain **(race, driver)** — one row per driver
per race. Carries points, positions, status, constructor. **The core table.**

**3. Qualifying vs. race day**
- Grid position vs. finishing position for each driver in each race?
- Which drivers most consistently gain positions (grid → finish delta)?
- Who converts pole to a win most often?

→ `grid_position` + `finish_position` on `fct_results`. A separate
`fct_qualifying` only if we want Q1/Q2/Q3 times or to distinguish *qualifying*
position from *grid* position (they differ under penalties).

**4. Head-to-head comparisons**
- Teammate vs. teammate: points / finishing position, race by race, within a season?
- Driver vs. driver across a shared season: finishing-position record?
- Constructor vs. constructor: who out-scored whom in a season?

→ No new fact — filtered self-joins on `fct_results`. Confirms `driver_key` and
`constructor_key` must both be present **on the results fact** (teammate = same
constructor, same race, different driver).

**5. Circuit / season context**
- Which circuits has a driver performed best/worst at historically?
- How has a constructor's performance trended across seasons (development arc)?
- Which season had the closest title fight?

→ Needs clean `dim_race` (circuit, season, round, date) and `dim_circuit` for
circuit-level rollups.

**6. Reliability**
- Each driver/constructor's DNF rate by season?
- Most common retirement reasons (engine, collision, gearbox, …)?

→ `status` on `fct_results`; categorical with a fixed value set → `accepted_values`
test (optionally a small `dim_status` grouping mechanical / collision / finished).

<h4 id="modelling-notes-open-decisions">Modelling notes — decisions (locked 2026-08-01)</h4>

- **Split standings by entity.** `fct_driver_standings` and
  `fct_constructor_standings` rather than one fact with a polymorphic
  `driver_or_constructor` key — a fact keyed on "sometimes a driver, sometimes a
  constructor" needs a nullable FK and breaks clean `relationships` tests.
- **Standings: ingest official + reconcile ✓ (decided).** Ingest the official
  per-round standings from the API (correct — accounts for penalties, sprint
  points, historical scoring), **and** reconcile a *derived* cumulative-points
  total against them as a data-quality test. Derive-only was rejected: it silently
  drifts from the record books.
- **~~SCD2 on `dim_constructor` rebrands~~ — REVERSED 2026-08-03 on Phase 0
  evidence.** The original reasoning was that rebrands (Toro Rosso → AlphaTauri
  → RB) are a constructor's identity history, and so earn Type 2. **The source
  does not model them that way.** Walking every constructor list from 1996 to
  2024, *no `constructorId` was ever observed carrying more than one name* —
  each rebrand is a distinct id with a contiguous, non-overlapping span. There
  is no changing attribute for SCD2 to track, so `valid_from`/`valid_to`/
  `is_current` would be structure with no varying data behind it.
  **`dim_constructor` is Type 1**, as is `dim_driver`. Evidence:
  [`discovery/findings/mutability.json`](../discovery/findings/mutability.json).
- **Consequence, accepted:** rebrands are separate constructors, exactly as the
  source models them. "How has this team performed across its rebrands" is
  **not** answerable without a hand-curated lineage mapping, which was
  considered and rejected as scope. Theme 5's per-constructor trend question is
  unaffected.
- **The project therefore demonstrates no SCD Type 2.** This is deliberate:
  none of these sources changes attributes in place, and building the pattern
  where the data does not call for it would be structure for its own sake.

## 7. Functional requirements

| ID | Requirement |
|---|---|
| FR1 | Extract from Jolpica-F1 (confirmed 2026-08-02): `seasons`, `circuits`, `races`, `drivers`, `constructors`, `results`, `qualifying`, `sprint`, `pitstops`, `driverstandings`, `constructorstandings`, `status`. `laps` is parked — see §12 |
| FR2 | Land raw JSON in object storage partitioned by ingestion date |
| FR3 | Load warehouse from the lake files (replayable) |
| FR4 | Re-running any step must not duplicate data (idempotent) |
| FR5 | Preserve **snapshot history** for attributes that change over time |
| FR6 | Record failed record-level ingestions for later retry (dead-letter) |
| FR7 | Transform into staging → dimensions → facts → aggregates |
| FR8 | Run on a schedule with alerting on failure |

## 8. Technical requirements (non-functional)

| ID | Requirement |
|---|---|
| NFR1 | **Cost: $0** — free tiers only |
| NFR2 | **Reproducible** — pinned dependencies; fresh clone runs with documented steps |
| NFR3 | **Secrets never committed** — verified before first commit |
| NFR4 | **Resilient** — retry with backoff on transient API errors from day one |
| NFR5 | **Rate-limit compliant** — respect API quotas |
| NFR6 | **Documented** — lineage/docs generated, not hand-maintained |
| NFR7 | **All timestamps UTC**; source epochs/strings converted at the staging layer |
| NFR8 | **Setup in < 15 minutes** from a fresh clone, README only |

## 8a. Volume targets

Measured 2026-08-02 against Jolpica-F1 (`discovery/probe_source.py --volumes`).
Page size is **100 rows, the observed hard cap**; call counts are ceiling
division on the measured totals.

| Dataset | Rows (all-time) | Backfill calls | Per scheduled run |
|---|---|---|---|
| Reference (seasons, circuits, drivers, constructors, status) | 1,386 | **16** | 1–2 (changed rows only) |
| `races` | 1,172 | **12** | 1 |
| `results` | 26,115 | **262** | 1 per race |
| `qualifying` | 11,212 | **113** | 1 per race |
| `sprint` | 568 | **6** | 1 per sprint round |
| Standings (driver + constructor) | per-round | **2,344** — 1 call per entity per race | 2 |
| `pitstops` | ~43/race | **1,172** — season-scoped calls rejected | 1 per race |
| `laps` | ~1,129/race | **~14,000** | — |

**Totals:** ~2,753 calls without pit stops or laps · ~3,925 with pit stops ·
~18,000 with laps.

**Decisions this forces:**

- **Laps are parked.** ~14,000 calls — more than three times the rest of the
  project combined — to serve no question in §6. Moved to the Parking Lot.
- **Standings dominate the remaining cost** at 2,344 calls, because per-round
  standings cannot be fetched season-wide. They stay: `fct_driver_standings`
  and `fct_constructor_standings` are MVP facts and Theme 1 depends on them.
- **Backfill once, then incremental.** The full backfill is a one-off; a
  scheduled run touches only the latest race.
- **Rate limits are unpublished in-band** — no rate-limit headers are returned,
  so the backfill must be paced client-side and be resumable.

## 8b. Dashboard specification

> `TODO (Phase 0/1):` define one visual per §6 question once the models exist.
> Every visual reads from `marts` only; entities display as **names**, not ids;
> screenshots embedded in the README.

## 9. Phases & timeline

> Phase 0 is a **hard gate**. Do not design the schema before it completes.

| Phase | Focus | Exit criteria |
|---|---|---|
| **0 — Discovery** | Validate every endpoint's real payload; prove storage + warehouse connectivity; confirm rate limits & pagination | Documented source inventory; smoke test connects to both systems |
| **1 — Ingestion** | API → lake → warehouse raw layer | Raw tables populated; idempotent re-run proven |
| **2 — Modelling** | Staging + star schema + tests | All models built, all tests green |
| **3 — Orchestration** | Scheduler/DAG, alerting, incremental | One successful scheduled end-to-end run |
| **4 — Serving & polish** | Dashboard, CI, docs, README | MVP checklist complete |

## 9a. Deliverable: repository README

The README is the highest-traffic artifact in the project — most reviewers read
it and never open a source file. Treat it as a deliverable with a defined shape.

**Required sections, in order:**

1. **One-line description** — what this is, in a sentence.
2. **Architecture diagram** — the system at a glance, rendered inline.
3. **What questions it answers** — the analytical questions from §6, with
   dashboard screenshots.
4. **Tech stack + why** — one line of rationale per component.
5. **Run it in 3 commands** — setup, ingest, transform.
6. **Data model** — the star schema, with a link to the published lineage docs.
7. **Engineering notes** — idempotency, retry/dead-letter, testing, CI.
8. **Attribution** — data source and its terms of use.

**Rules**
- Lead with the diagram and the results; put setup instructions below them.
- Every claim in the README must be true of the committed code.
- Assume 90 seconds of attention: the top third must carry the whole story.

## 10. Success metrics

| Metric | Target |
|---|---|
| Sources ingested | ≥ the entities needed for §6 (set count in Phase 0) |
| Model test coverage | 100% of models have a tested grain |
| Test pass rate | 100% green on every build |
| Pipeline run | End-to-end scheduled run succeeds unattended |
| Dashboard | ≥3 questions from §6 answered visually |
| Setup time from clone | < 15 minutes following the README |
| Explainability | Author can whiteboard the architecture and defend each decision unaided |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Source API instability / deprecation (cf. Ergast shutdown) | Validate the live endpoint + terms in Phase 0; land raw so the warehouse is rebuildable |
| Upstream transient 5xx | Retry + backoff + dead-letter from day one |
| Free-tier storage limits | Cap high-cardinality volume; monitor warehouse size |
| Scope creep | This PRD + Parking Lot; review before starting any new work |
| Schema built on wrong assumptions | **Phase 0 discovery gate** before schema design |

## 12. Parking lot

> Ideas that are **not** in scope. Capture them here instead of building them.
> Revisit only after the MVP checklist is complete.

| Idea | Why parked |
|---|---|
| Live-timing / telemetry streaming | Scope monster; batch satisfies all §6 questions |
| Full telemetry backfill (OpenF1 car data) | Expensive relative to its analytical value at MVP |
| **Lap timings (`laps`)** | Measured at ~14,000 backfill calls — 3× the whole rest of the project — and answers no §6 question. Parked 2026-08-02 |
| ML race/lap prediction | Not a data-engineering signal |
| Governed semantic layer / NL query interface | Built in the author's professional project over BigQuery instead |

---

## Change control

Any new idea must pass this test before it is built:

1. Which **goal (§3)** does it serve?
2. Which **success metric (§10)** does it move?
3. Is the **MVP (§5)** already complete?

If the answer to any of these is unsatisfying → **Parking Lot**.
