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
| **Status** | Phase 1 complete (backfill deferred) — Phase 2 next |
| **Last updated** | 2026-08-09 |
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

- [x] Ingestion runs end-to-end from a single command, idempotently.
      `python tasks.py ingest --season 2026 2025` — reference data, session
      facts and race-scoped entities in dependency order. Re-running inserts
      and updates nothing.
- [x] Raw data lands in object storage, partitioned by ingestion date.
- [x] Warehouse loads from the lake, not from memory.
- [ ] Staging models exist for every source, each with a tested grain.
- [ ] A star schema with ≥3 dimensions and ≥2 fact tables.
- [ ] Data quality tests pass on every model (grain + key integrity minimum).
- [ ] One orchestrated scheduled run (DAG) that succeeds end to end.
- [ ] A dashboard answering ≥3 real analytical questions.
- [ ] README with architecture diagram and a minimal run sequence (§9a).

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

_Written 2026-09-15, once the models existed. §10 requires **≥3 of the §6
themes answered visually**; this specifies four._

**Tool: Metabase, self-hosted in Docker. Screenshots in the README, not a
hosted link.** Decided 2026-09-15 after evaluating Evidence.dev, Streamlit and
Looker Studio. Reasoning, since the obvious choice would have been a shareable
URL:

- **A link nobody maintains is worse than a screenshot.** A dead dashboard URL
  in a README reads as abandoned work; a screenshot stays true indefinitely and
  needs nothing. The author's own assessment was that the link would not be
  maintained past six months, and designing around that is more honest than
  designing around good intentions.
- **Free hosting for Metabase does not really exist.** It is a stateful JVM
  application needing ~2 GB and an always-on process — Vercel and Netlify cannot
  run it at all, Render's free tier is too small, Railway's is gone, and
  Metabase Cloud is ~$85/month. The only free routes are a self-managed VM (an
  ops project) or nothing.
- **A static alternative was the real contender.** Evidence.dev builds to a
  static site, hosts free, cannot rot and exposes no database — but it adds a
  Node toolchain to a Python repository that CI cannot lint or test, and its
  queries are invisible to every existing check.
- **Screenshots reach everyone.** Every reader of the README sees them; a link
  is opened by a minority. The dashboard is not this project's differentiator —
  the decision log, the reconciliation test and the backfill findings are.

The cost accepted: reviewers cannot interact with it, and it is demonstrated
live in interviews instead.

**Connection.** Metabase connects as **`f1_reporting`** (migration 007) — a
read-only role with `select` on `marts` and nothing else. Never as
`f1_pipeline`, which owns those tables and could drop them. Metabase stores that
credential in its own application database, which is a further reason the
credential should be able to do nothing but read.

### Rules

- **Every visual reads from `marts` only.** No `raw`, no `staging` — the
  reporting role cannot reach them, which enforces this rather than trusting it.
- **Entities display as names, not ids.** `dim_driver.full_name`,
  `dim_constructor.constructor_name`, `dim_race.race_name`.
- **Never `classified_position` for "did they finish".** Use
  `fct_results.is_classified`; the two disagree, and the fact's flag reads the
  stewards' marker. Likewise never `dim_status.status_implies_running_at_end`
  for DNF rate — it answers a different question (ARCHITECTURE decision 39's
  neighbour; see `_facts.yml`).
- **Never sum `fct_driver_standings.points` across rounds.** It is a periodic
  snapshot carrying a running total; summing counts every point once per
  subsequent round. Difference consecutive snapshots, or use `fct_results`.
- **Screenshots embedded in the README**, replacing the "screenshots land with
  the serving phase" note in §9a.3.
- **State the denominator on any rate.** DNF rate is computed **per start, not
  per entry** — a car that never started did not fail to finish, so
  `withdrawn_or_dns` rows leave both halves of the fraction. Measured, the two
  definitions differ by 0.1–3.4 points across the eras, widest in the 1960s
  (48.3% per entry against 44.9% per start). A rate with an unstated denominator
  is one two readers can interpret differently.

_Visual 2's source was specified as `fct_results` × `dim_race` and corrected on
building it: `season` is denormalised onto the fact (ARCHITECTURE decision 27),
so no race join is needed to slice by era — that decision paying off. `dim_status`
is joined instead, to exclude non-starters._

### The visuals

| # | Visual | §6 theme | Reads | Shape |
|---|---|---|---|---|
| 1 | **Championship progression** — points by round for the top contenders of a season | 1 | `fct_driver_standings` × `dim_driver` × `dim_race` | Line, one series per driver, x = `round` |
| 2 | **Reliability by era** — DNF rate per decade across 77 seasons | 6 | `fct_results` × `dim_status` | Bar, x = decade, y = share where `not is_classified` |
| 3 | **Why cars retire** — retirement causes, grouped | 6 | `fct_results` × `dim_status` | Bar, `status_category` over non-finishers |
| 4 | **Grid vs finish** — drivers who gain the most places | 3 | `fct_results` × `dim_driver` | Bar, mean `positions_gained`, minimum start count |
| 5 | **Data freshness** — how current the warehouse is | — | `rpt_pipeline_freshness` | Table / number cards |
| 6 | **Driver standings** — the championship as it stands | 1 | `fct_driver_standings` × `dim_driver` × `dim_constructor` | Table, latest round |
| 7 | **Constructor standings** — the same for teams | 1 | `fct_constructor_standings` × `dim_constructor` | Bar, latest round |
| 8 | **Calendar growth** — races per season, 1950–2026 | 5 | `dim_race` | Line, scheduled vs run |
| 9 | **Circuit map** — 78 circuits in 34 countries | 5 | `dim_circuit` × `dim_race` | Map, sized by races held |
| 10 | **Teammate head-to-head** — who beat their teammate | 4 | `fct_results` self-joined × `dim_driver` × `dim_constructor` | Bar, two series per pairing |

### Two tabs, not one dashboard

**`Current season`** — visuals 5, 6, 7 and 1 (with `season` defaulted to the
current year). **`All time`** — visuals 2, 3, 4, 8 and 9.

One dashboard serving both purposes serves neither: a reviewer wants the
77-season sweep, someone checking the championship wants last weekend. Splitting
them keeps one link while letting each tab answer one question.

### The reporting layer starts here, for a reason

ARCHITECTURE §1 records that no aggregate layer exists and that **the trigger
for starting one is a need the star schema cannot serve, not volume**. Visual 5
is that need.

The honest answer to "how current is this?" is *when the pipeline last ran*, and
that lives in `raw.ingestion_checkpoints` — which the reporting role deliberately
cannot read (migration 007). The available alternative, `max(ingested_at)` on the
facts, answers a different question: **when data last changed**. The upsert only
writes when a payload differs, so a run that succeeds and correctly finds nothing
new leaves it untouched.

Measured on 2026-09-17: ingestion ran at 03:06 while the newest data change read
2026-09-14 — three days apart. A card built on `ingested_at` would have called a
pipeline stale that had run twenty seconds earlier.

So `rpt_pipeline_freshness` surfaces the signal into `marts` through a tested
model rather than widening a security boundary to answer one question. It
needed no new grant: the default privileges in migration 007 already covered
marts tables that did not exist when they were written.

### All six §6 themes now have a visual

Theme 4 is visual 10, and it is the one the schema was shaped for: `driver_key`
and `constructor_key` both sit on `fct_results` precisely so "same race, same
team, different driver" is a self-join on two keys rather than a bridge table.

Its methodological decision is stated on the query: **the record counts only
races where both drivers were classified.** A teammate who retires on lap 3 is
not evidence the other was faster, and counting it would make a reliability
table wear a pace table's name. Points are counted across all races, because
points are the outcome and reliability is part of it — so a pairing where one
driver leads the head-to-head and the other leads on points is the interesting
case, not an inconsistency. Zhou and Bottas in 2024 are exactly that.

Nine rather than the three §10 requires, so no single fact carries the dashboard
and every mart is used by something: the snapshot facts drive 1, 6 and 7, the
transaction fact drives 2, 3 and 4, the dimensions alone drive 8 and 9, and 5
reads the reporting model. Together they demonstrate the star schema being used
as designed rather than one wide table queried nine ways.

**Visual 2 is the headline.** 50.1% DNF in the 1950s falling to 13.0% in the
2020s is the clearest evidence the 77-season backfill bought something a
three-season sample could not — and it is the one a reader will remember.

## 9. Phases & timeline

> Phase 0 is a **hard gate**. Do not design the schema before it completes.

| Phase | Focus | Exit criteria |
|---|---|---|
| **0 — Discovery** | Validate every endpoint's real payload; prove storage + warehouse connectivity; confirm rate limits & pagination | Documented source inventory; smoke test connects to both systems |
| **1 — Ingestion** ✅ | API → lake → warehouse raw layer; **CI gate live** (moved from Phase 4, see Change control) | Raw tables populated; idempotent re-run proven; CI green on every PR — **met 2026-08-09.** All 12 in-scope endpoints ingest. Reference data covers all of history; facts cover 2024–2026 (59 of 1,172 races). The historical backfill is deliberately deferred — see ARCHITECTURE decision 25 |
| **2 — Modelling** | Staging + star schema + tests | All models built, all tests green |
| **3 — Orchestration** | Scheduler/DAG, alerting, incremental | One successful scheduled end-to-end run |
| **4 — Serving & polish** | Dashboard, CI, docs, README | MVP checklist complete |

## 9a. Deliverable: repository README

The README is the highest-traffic artifact in the project — most reviewers read
it and never open a source file. Treat it as a deliverable with a defined shape.

**Required sections, in order:**

1. **One-line description** — what this is, in a sentence.
2. **Architecture diagram** — the system at a glance, rendered inline.
3. **What questions it answers** — the analytical questions from §6. Carry a
   real worked result from the built models; add dashboard screenshots when
   the serving phase produces them. A placeholder image is worse than a
   table, because it breaks the "every claim is true" rule on sight.
4. **Tech stack + why** — one line of rationale per component.
5. **Run it** — the shortest sequence that actually works from a fresh
   clone. **Amended 2026-09-14:** this said "in 3 commands — setup, ingest,
   transform", which the implementation does not support. A clone also
   needs `.env` filled in and `python tasks.py migrate` run, so the honest
   sequence is four commands around one config step. Folding `migrate`
   into `setup` to hit the number was rejected: applying schema changes as
   a side effect of installing dependencies hides the one step that
   touches the database.
6. **Data model** — the star schema, with the command that generates the
   lineage docs. A hosted link replaces it if the docs are ever published.
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

### Approved changes

**2026-08-04 — CI moved from Phase 4 to Phase 1.** Lint and unit tests run on
every pull request from Phase 1 rather than at the end.

1. **Goal:** G6 (documented, reviewable) and the MVP's "tests pass on every
   model" — a gate that only appears at the end has not gated anything.
2. **Metric:** *Test pass rate — 100% green on every build.* Unenforceable
   without CI; the metric existed with no mechanism behind it.
3. **MVP complete?** No — but this is not new scope. CI was **already in scope**
   for Phase 4 (§9, ARCHITECTURE §13). Only its *timing* changed, so nothing is
   added to the build.

> **Correction, 2026-08-08.** The paragraph below claimed CI "converts [the
> pull request] into a gate that can actually refuse a merge." **It does not,
> and did not.** Blocking a merge on a status check requires branch protection,
> which GitHub does not offer for **private** repositories on the Free plan. CI
> here is **advisory**: it reports, it cannot refuse. Two pull requests (#4, #5)
> merged with red checks before this was noticed, which is the evidence.
>
> The move to Phase 1 still stands — advisory CI caught a real defect that local
> runs had missed. But the claim was overstated and is corrected here rather
> than left to read as true.
>
> **What is in place instead:** a committed `pre-push` hook running the same
> `ruff` and `pytest` commands as CI, installed with
> `git config core.hooksPath hooks`. It stops a broken commit from reaching the
> remote at all, which is earlier than a merge gate would. It is a **guardrail,
> not a gate** — `--no-verify` bypasses it, and a different machine that has not
> run the install command has no hook at all.
>
> **When this becomes a real gate:** when the repository goes public in Phase 4,
> branch protection becomes available at no cost and `lint-and-test` should be
> made a required status check on `main`.

**Why it moved:** SECURITY §7 requires pull requests, and the project is solo.
Self-review catches nothing — the author and reviewer are the same person — so
without CI the pull-request requirement is ceremony with a real time cost. CI is
what converts it into a gate that can actually refuse a merge. Either CI moves
forward or the PR requirement should be dropped; keeping both while one is
inert is the worst of the three.

**Scope kept deliberately small:** `ruff` and `pytest` only. `dbt build` and
`sqlfluff` stay in Phase 4 because no models or SQL exist yet, and a check that
validates nothing passes regardless and teaches you to trust it.

**Convention adopted alongside it:** pull requests are opened **per unit of
work** (a phase slice), not per commit.
