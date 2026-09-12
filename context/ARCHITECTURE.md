# Design & Architecture — F1 Analytics Pipeline

> Companion to [PRD.md](PRD.md). This document defines **how** the system is
> built and **why** each choice was made. Decisions are logged in §7.

---

## 1. Top-level system overview

```mermaid
flowchart LR
    A[F1 API — confirm in Phase 0] -->|extract| B[Python ingestion]
    B -->|land raw JSON| C[(S3 data lake<br/>partitioned by date)]
    C -->|load from lake| D[(PostgreSQL / Supabase<br/>raw schema)]
    D -->|dbt| E[staging views]
    E -->|dbt| F[marts: dim + fct]
    F -->|dbt| G[aggregates / marts]
    G --> H[BI dashboard]
    I[Airflow DAG] -.orchestrates.-> B
    I -.orchestrates.-> E
    B -->|failures| J[(dead-letter table)]
```

| Layer | Technology | Responsibility |
|---|---|---|
| Source | F1 REST API _(confirm in Phase 0)_ | System of record (external) |
| Ingestion | Python + requests | Extract, retry, paginate |
| Lake | AWS S3 | Immutable raw landing zone, replay source |
| Warehouse | PostgreSQL (Supabase) | Structured storage & compute |
| Transformation | dbt Core | Staging → dimensional model, tests, docs |
| Orchestration | Airflow (Docker) | Scheduling, dependencies, alerting |
| Serving | Metabase / BI | Dashboards |
| CI | GitHub Actions | Run tests on PR |

## 2. Design methodology

- **ELT, not ETL** — land raw first, transform in the warehouse. Raw data is
  never modified in place, so transformations are always replayable.
- **Layered (medallion-style) architecture** — `raw` → `staging` → `marts`.
  Each layer has exactly one job; no layer reaches past its neighbour.
- **Dimensional modelling (Kimball)** — conformed dimensions and fact tables at
  a declared grain, optimised for analytical queries and BI.
- **Declarative transformations** — dbt owns the DAG; dependencies are derived
  from `ref()`, never hand-ordered.

### Layer contract

| Layer | Materialisation | Allowed to do | Must NOT do |
|---|---|---|---|
| `raw` | tables | Store source payloads as-landed | Business logic, filtering |
| `staging` | views | Rename, cast, convert epochs, light cleaning | Joins, aggregation, business rules |
| `marts` | tables | Joins, business logic, surrogate keys, aggregation | Re-clean raw data |

## 3. Data flow

1. **Extract** — call the API with retry/backoff; paginate where the endpoint
   caps results.
2. **Land** — write raw JSON to `s3://<bucket>/raw/<entity>/<YYYY-MM-DD>/…`.
3. **Load** — read those objects back from S3 and upsert into the `raw` schema.
   The warehouse loads **from the lake**, never from the in-memory API response.
4. **Transform** — dbt builds `staging` views, then `marts` tables.
5. **Test** — dbt tests run as part of every build (`dbt build`).
6. **Serve** — BI reads only from `marts`.

> **Rule:** the implementation must match this diagram. If the architecture
> states the warehouse loads from the lake, the load step reads from lake
> objects — not from data still held in memory. Ambiguity here produces dead
> code and an architecture that cannot be explained honestly.

## 4. Design patterns

| Pattern | Where | Why |
|---|---|---|
| **Idempotent upsert** | All raw loads | Re-running a step never duplicates rows |
| ~~**Append-only snapshots**~~ | _not used_ | Phase 0 found no source that mutates an attribute in place; history is carried in natural keys instead. See SCHEMA §2 |
| **Immutable event append** | Match facts | Matches never change once played |
| **Retry with exponential backoff** | All API calls | Upstream 5xx/timeouts are routine, not exceptional |
| **Dead-letter log** | Record-level failures | Failures are recoverable, not silently lost |
| **Cursor pagination** | List endpoints | Work around per-call row caps |
| **Surrogate keys** | Dimensions | Decouple warehouse keys from source keys |
| **Unknown member** | Dimensions | Facts with unmatched FKs still join (no row loss) |
| **Incremental models** | Large facts | Process only new data per run |

### Classifying every source (do this in Phase 0)

The most important design question, asked **per source**, before any table is
created:

| Question | Load pattern |
|---|---|
| Immutable event? (a played match) | Insert, `ON CONFLICT DO NOTHING` |
| Mutable reference data, history not needed? | Upsert (`DO UPDATE`) |
| **Attributes change over time and history matters?** | **Append-only with `snapshot_date`** |

> **Rule:** an overwrite upsert discards the previous value. Any source whose
> history is needed for SCD Type 2 or trend analysis must be append-only from
> the start — this cannot be reconstructed later, because the overwritten
> values are gone.

## 5. Failure handling

| Failure | Response |
|---|---|
| Transient API error (429/5xx/timeout) | Retry with exponential backoff |
| Persistent failure on one record | Log to dead-letter table; continue the run |
| Non-transient error (404, bad request) | Fail fast — do not retry |
| Whole-run failure | Alert via orchestrator; run is re-runnable (idempotent) |

**Principle:** one bad record must never kill an entire run, and must never
vanish silently.

## 6. Conventions

> Agreed **before** coding. Conventions decided ad hoc during implementation
> produce inconsistency that has to be reworked across every model.

**Naming**
- Staging `stg_<source>` · Dimensions `dim_<entity>` · Facts `fct_<event>` ·
  Aggregates `mart_<subject>`
- Columns `snake_case`; booleans read as assertions (`is_`/`has_`); no reserved
  words
- Rename vague source columns at staging (`name` → `team_name`)

**Time**
- All timestamps **UTC**
- Source epochs converted to real timestamps at **staging**
- Keep the raw epoch alongside the derived timestamp for traceability

**Grain**
- Every model declares its grain in its description **and enforces it with a
  test**
- Composite grains use a multi-column uniqueness test

**Keys**
- Never include a nullable column in a primary key
- Prefer a guaranteed-present natural key over a sometimes-null identifier
- Confirm nullability from **observed data**, not documentation

**Testing**
- Test what downstream logic depends on; do not test descriptive fields
- Tiers: (1) grain/PK — always · (2) join keys, categoricals — usually ·
  (3) free text, metrics — rarely

## 7. Decision log (ADR-lite)

> Record the alternatives considered and the trade-off, not just the outcome.
> Add a row whenever a non-obvious choice is made.

| # | Decision | Alternatives considered | Rationale |
|---|---|---|---|
| 1 | ELT with a lake landing zone | Direct API → warehouse | Raw is replayable; transformations can be rebuilt without re-hitting the API |
| 2 | Warehouse loads **from the lake** | Parallel write to lake and warehouse | Single source of truth; the load step is independently replayable |
| 3 | Append-only snapshots for changing attributes | Overwrite upsert | Overwrite destroys the history SCD2 and trend models require. **Not triggered by any F1 source** (see 18, 19) — the rule stands, but no source meets it |
| 4 | Postgres (Supabase) as warehouse | BigQuery, Snowflake, Databricks, MotherDuck/DuckDB | Free and always-on; pipeline patterns are warehouse-agnostic. See §7.1 |
| 5 | dbt Core | Dataform, hand-written SQL | Industry standard for the target roles; tests, docs, and lineage built in |
| 6 | Retry + dead-letter from the first implementation | Add resilience later | Transient upstream failures are routine; retrofitting loses records |
| 7 | Airflow as orchestrator | Dagster, Prefect, cron/CI schedules | Heavier to run, but the most widely recognised orchestrator in the target job market |
| 8 | **Jolpica-F1 as the single source** (`api.jolpi.ca/ergast/f1`) | OpenF1, FastF1, Ergast | All 13 endpoints needed for PRD §6 returned 200 in Phase 0. Ergast is shut down; OpenF1 covers only telemetry (already parked); FastF1 is a library, so ingestion would be a cached wrapper call rather than an HTTP client with retry and dead-letter — the engineering this project exists to demonstrate |
| 9 | `discovery/` is separate from `ingestion/` | Put probes in `ingestion/`; delete after Phase 0 | Discovery code is evidence-gathering, not pipeline code, and must never be mistaken for it. Kept (not deleted) because the findings justify the schema; isolated one-way so neither side can import the other |
| 10 | Client-side pacing, not header-driven throttling | Read rate-limit headers at runtime | The API returns **no** rate-limit headers on any endpoint, so remaining allowance cannot be read. Budget comes from the published policy, enforced by deliberate pacing plus `Retry-After` on 429 |
| 11 | Page size fixed at the observed cap of 100 | Assume a larger page; read the cap from the response | `limit=2000` returns HTTP 200 with `"limit": "100"` — the server **silently clamps**. A backfill assuming a larger page would stop short and report success |
| 12 | Backfill at **season** scope, not race scope | One call per race | Season-scoped `results` returns 479 rows in 5 calls; race-scoped needs one call per race for the same data. Roughly a 5× reduction against a source with no published burst allowance. Does not apply to `pitstops`, `laps` or per-round standings, which reject or ignore season-scoped calls |
| 13 | **`laps` parked, not ingested** | Ingest capped (recent seasons only); ingest in full | Measured at ~14,000 backfill calls versus ~2,750 for everything else combined, and answers no question in PRD §6. A capped subset was rejected too: partial lap coverage invites analysis that silently excludes most of the sport's history |
| 14 | Strict `relationships` tests; Unknown member built but not load-bearing | Lenient FK tests; skip the Unknown member entirely | Join coverage measured at 100% over all 26,115 result rows (1950→2024), so strict tests will not produce false failures. The Unknown member is still built, because a source that later emits an unmatched key must route the row there rather than lose it to an inner join |
| 15 | `not_null` tests only on the six fields present in every era | Derive nullability from a recent-season sample | `FastestLap` is absent before 2000 and `Time` is present in 16–23% of rows pre-2000, but a 2024 sample reports them at 97% and 90%. Tests written off the modern sample would pass in development and fail once the backfill reached the 1990s |
| 16 | Backfill paced to the **hourly** budget (500/hr) and checkpointed as resumable | Pace to the 4/s burst limit; run the backfill in one pass | Documented limits are 4 req/s burst **and 500 req/hr sustained**. The hourly budget binds first: 4/s would exhaust it in ~2 minutes. At ~3,925 calls the backfill spans ~8 hours, so it must survive interruption and resume without re-fetching — which is idempotency (decision 6) being load-bearing rather than decorative |
| 17 | Repository licensing split: code separate from data | Single repository licence | Source data is CC BY-NC-SA 4.0, whose ShareAlike clause reaches adaptations of the *data* — the marts and any published extract — but not the code that produces them. One blanket licence would either over-claim the data or wrongly bind the code. See SECURITY §3 |
| 18 | **`dim_constructor` is Type 1 — reverses the earlier Type 2 decision** | Keep Type 2; Type 1 plus a hand-curated lineage seed | Walking every constructor list 1996–2024, no `constructorId` was ever observed carrying two names: rebrands are separate ids upstream. With no attribute changing in place, Type 2 would produce `valid_from`/`valid_to`/`is_current` columns over single-version rows. The lineage seed was rejected as scope; the cost is that cross-rebrand team continuity is not answerable, which is accepted |
| 19 | No SCD Type 2 anywhere in the project | Manufacture a Type 2 use case to demonstrate the technique | Decision 18 removed the only candidate. Every remaining source carries history in a natural key. Building the pattern where the data does not call for it is structure for its own sake, and is visible as such to anyone who queries the table |
| 20 | Warehouse reached via Supabase's **session pooler**, not the direct host | Direct connection; transaction pooler (port 6543) | The direct endpoint is IPv6-only on new projects and unreachable from most IPv4 networks — the risk §9 anticipated. The transaction pooler holds no session state, which breaks dbt. Session pooler verified working first attempt |
| 21 | Backfill runs **newest season first** | Oldest-first (chronological) | A working dashboard needs one complete recent season, not seventy partial ones, so newest-first produces something demonstrable within the first hour of an 8-hour job. Chronological order has no technical advantage here because loads are idempotent and order-independent. The cost is that the sparse early decades (see the era profile) arrive last, so their edge cases surface late — mitigated by already having measured them in Phase 0 |
| 22 | Prove **one endpoint end to end** before generalising | Build all extractors, then the lake loader, then the warehouse loader | The risky part is the *vertical* path — API → lake → warehouse — not the twelfth extractor. Proving it once surfaces path conventions, idempotency and checkpointing while there is one thing to debug. Building horizontally would multiply an unproven pattern twelve times before discovering it is wrong |
| 23 | Checkpoint state lives **in the warehouse** | A local file; an object in the lake | The backfill spans ~8 hours (decision 16) and must resume. A local file does not survive a machine change and is invisible to the orchestrator; a lake object needs a read-modify-write with no transactional guarantee. The warehouse gives transactional updates alongside the data the checkpoint describes, so state and data cannot disagree |
| 25 | **Historical backfill deferred until Phase 2 has models** | Backfill now, while ingestion is fresh; backfill incrementally as each endpoint lands | The machinery is ready — checkpointed per round, budget shared across restarts, loads idempotent — so this is about sequencing, not capability. ~3,900 calls at 500/hour is an ~8-hour unattended run, and today nothing downstream could tell whether the result was *correct*: 26,000 rows in `raw` with no staging models to test grain, coverage or era-dependent nullability against. Running it after the star schema exists means the tests that would catch a bad backfill already exist when it runs. The cost of waiting is nil — the data is 75 years old and re-fetchable; the cost of a silently wrong 8-hour load discovered in Phase 2 is doing it twice. Three current seasons (2024–2026) already exercise every code path |
| 24 | **Rate budget also lives in the warehouse** (`raw.api_call_log`), on its own connection | In-process counter; a local file; reading rate-limit headers | The API returns no rate-limit headers, so the budget must be counted client-side — and an in-process counter is blind across processes and resets on restart. Measured: a three-season ingest spent ~120 calls while no single process saw more than 72, and a resumed backfill would start counting from zero inside the same hour. A **separate connection** because recording a call must commit to be visible to other processes, and committing on the pipeline's connection would also commit whatever upsert was in flight. The burst limit stays in-process: a round-trip per request would cost more than it protects, and the hourly budget is the one that binds |
| 26 | **Facts coalesce unmatched keys to the Unknown member; a singular test detects drift** | Inner-join the dimensions; rely on `relationships` alone | SCHEMA §5 requires facts to map unmatched foreign keys to Unknown. An inner join would drop the row instead, and a dropped fact row is invisible — the total simply comes out lower and nothing errors. The consequence, named rather than discovered later: coalescing makes the `relationships` tests **vacuous**, since a coalesced key always resolves to a row that exists. `assert_no_facts_on_unknown_members` is what detects drift now. The pair is deliberate — `relationships` proves the key resolves, the singular test proves it resolved to something real |
| 27 | **`season` and `round` denormalised onto `fct_results`** | Keep the fact purely keyed and join `dim_race` for every slice | The line drawn is to denormalise what is *filtered on* constantly, not what is *joined for*. Almost every query slices by season, and forcing a dimension join for that is friction with no benefit. Driver, constructor and status names are deliberately **not** carried, because those are joined for, and duplicating a name invites two sources of truth for it |
| 28 | **Sprint results get their own fact, not a `session_type` column on `fct_results`** | Union sprint into `fct_results` with a session discriminator; leave sprint out of the marts entirely | Found while building the standings facts: the points reconciliation PRD §6 commits to does not work from `fct_results` alone — official 437 for Verstappen in 2024 against 399 derived. Race plus sprint reconciles exactly, 24 of 24 drivers, zero residual. A `session_type` column was tempting (`stg_sprint` was given identical column names so a union would be possible) but would make **every existing query wrong by default**: finishing order, DNF rate and positions gained would all include sprint rows unless the author remembered the filter. A fact whose grain requires a filter to be correct is a reliable source of wrong numbers. Separate facts are right by default, match the shape already used for `fct_qualifying`, and confine the cost to one explicit union where total points are needed |

> Rows 1–7 are stack/pattern choices that carry over from the previous project
> and are independent of the data source. **F1-specific decisions — source
> selection, volume caps, load-pattern classification, connection endpoints,
> type/nullability calls — get logged here as they are made during F1 Phase 0
> and beyond.** Do not carry any source-specific finding over without
> re-validating it against the F1 API.

### 7.1 Warehouse choice — trade-offs

The most consequential trade-off in the stack, and the one most likely to be
questioned. Recorded here in full.

**The trade-off:** PostgreSQL is a row-store OLTP database, not a columnar MPP
analytical warehouse. The warehouses named in most job specifications
(Snowflake, BigQuery, Databricks, Redshift) are columnar systems built for
analytical scan performance.

**Why Postgres is chosen anyway:**

| Factor | Reasoning |
|---|---|
| **Scale** | At this project's data volume (tens of thousands of rows), columnar storage and MPP provide no measurable benefit. The engine is not the bottleneck. |
| **Cost & availability** | Must be free *and* always-on. Most managed analytical warehouses offer only time-limited trials, which expire and leave the project broken. |
| **Portability of the patterns** | The techniques demonstrated — idempotent loads, layered modelling, dimensional design, incremental processing, testing — are warehouse-agnostic. They transfer to any engine. |
| **Focus** | Re-platforming costs days of migration and retesting for no functional gain. That effort is better spent on the dimensional model, tests, and documentation, which are what actually differentiate the work. |

**What is given up:**
- Columnar scan performance and MPP scale-out (irrelevant at this volume).
- The keyword recognition of a warehouse name that appears on job specifications.
- Warehouse-specific features (clustering, partition pruning, time travel).

**Alternatives assessed:**

| Option | Verdict |
|---|---|
| **BigQuery free tier** | Strongest free analytical warehouse (permanent free allowance, genuinely columnar). Rejected here only because the goal is to demonstrate an open-source stack distinct from existing cloud-warehouse experience. |
| **MotherDuck / DuckDB** | Genuinely columnar with a mature dbt adapter and minimal infrastructure. The most likely future swap if a columnar engine becomes desirable. |
| **Databricks Free Edition** | Viable; heavier setup than the project needs. |
| **Snowflake, Redshift, ClickHouse Cloud** | Rejected — trial-limited, so the project would stop working once credits expire. |
| **Neon, Aiven, Render Postgres** | Equivalent to the current choice; row-store, no analytical advantage. |

**Revisit if:** data volume grows beyond a few million rows, query latency
becomes a real constraint, or a columnar engine is needed to demonstrate a
specific capability. Any migration happens **after** the MVP is complete, never
in place of it.

**Operational notes for this choice:**
- Free-tier managed Postgres may **pause after a period of inactivity**. A daily
  scheduled run keeps the project active — an intentional benefit of the
  orchestration layer, not a coincidence.
- Free-tier storage is capped; monitor size and keep high-volume tables bounded
  (set the concrete caps during F1 Phase 0).
- Object storage free tiers may be time-limited on new accounts; data volume here
  is small enough that post-expiry cost is negligible, but it is not free forever.

## 8. Environments

| Environment | Purpose |
|---|---|
| `dev` | Local development; personal schema; small data volumes |
| `prod` | Scheduled runs; full volumes |

Configuration is environment-variable driven; **no credentials in code or
committed config**. See [SECURITY_AND_GOVERNANCE.md](SECURITY_AND_GOVERNANCE.md).

## 9. Key implementation notes

- **Rate limiting** — respect documented quotas; pace requests deliberately.
- **Pagination** — list endpoints cap rows per call; page with a cursor until
  the target count is reached.
- **Partitioning** — lake objects partitioned by ingestion date for replay and
  traceability.
- **Idempotency proof** — running the pipeline twice must leave row counts
  unchanged. Treat this as an acceptance test, not an assumption.
- **Connection endpoints** — managed databases may require a pooler endpoint
  rather than the direct host, and the direct host may not be reachable on all
  networks. Confirm the working connection string in Phase 0.

## 10. Repository structure

```
.
├── CLAUDE.md                  # entry point: points at context/ before any work
├── README.md                  # the artifact reviewers actually read
├── context/                   # planning docs (this folder)
├── discovery/                 # Phase 0 probes + findings; throwaway, never imported
│   ├── probe_source.py
│   └── findings/              # committed evidence behind the schema decisions
├── ingestion/                 # extraction + load to lake + load to warehouse
│   ├── config.py             # single source of env-var config (§6)
│   ├── extract.py
│   ├── load_lake.py
│   ├── load_warehouse.py
│   └── pipeline.py            # CLI entry point
├── migrations/                # ordered, committed schema changes
├── dbt/
│   ├── models/
│   │   ├── staging/
│   │   └── marts/{dimensions,facts,aggregates}/
│   ├── macros/
│   ├── tests/                 # singular (custom) data tests
│   ├── dbt_project.yml
│   └── profiles.yml           # env_var references only
├── airflow/
│   ├── dags/
│   └── docker-compose.yml
├── tests/                     # Python unit tests (pytest)
├── .github/workflows/         # CI
├── .env.example
├── .gitignore
└── requirements.txt           # or pyproject.toml
```

**Rules**
- One responsibility per module; `pipeline.py` orchestrates, it does not
  implement extraction or loading.
- Nothing outside `ingestion/` talks to the source API **in the pipeline**.
  `discovery/` is the one exception: it exists to call the API before the
  pipeline exists. It is one-way isolated — `discovery/` never imports from
  `ingestion/`, and `ingestion/` never imports from `discovery/`. Findings
  move between them as documented facts, not as shared code.
- Nothing outside `dbt/` writes to the `staging` or `marts` schemas.

## 11. Development environment

| Item | Decision |
|---|---|
| Python version | Pin one version; record it in the README and CI |
| Environment | Virtual environment, created from a committed dependency file |
| Dependencies | **Pinned versions** — reproducibility is a stated requirement |
| Task runner | A `Makefile` (or equivalent) wrapping the common commands |

**Target: a fresh clone is running in under 15 minutes**, following the README
only. The command surface should be small enough to fit in three lines:

```
make setup      # create venv, install pinned dependencies
make ingest     # run the pipeline end to end
make transform  # dbt deps + dbt build (models + tests)
```

Wrapping commands in a task runner also removes the need to remember long
invocations, and gives CI and the README a single source of truth.

## 12. Testing strategy (application code)

Data quality tests are covered in
[SECURITY_AND_GOVERNANCE §4](SECURITY_AND_GOVERNANCE.md#4-data-quality-framework).
This section covers the **Python code**, which data tests do not reach.

| Test type | Scope | Runs |
|---|---|---|
| **Unit** | Pure logic, external calls mocked | Every commit / CI |
| **Integration (smoke)** | Real connectivity to storage and warehouse | Manually / scheduled, not in PR CI |
| **Acceptance** | Idempotency: run twice, row counts unchanged | Before merging pipeline changes |

**What is worth unit testing:**
- **Pagination loop** — terminates correctly, requests the right cursor, returns
  the requested number of records without duplicates.
- **Retry logic** — retries on transient failures, does **not** retry
  non-transient ones, gives up after the configured attempts. Branching logic
  like this is where silent bugs hide.
- **Parsing and type conversion** — timestamp/epoch handling, field extraction,
  handling of missing or null fields.
- **Configuration loading** — required settings are present and validated.

**What is not worth unit testing:** thin wrappers around library calls, and
anything whose only behaviour is delegation.

**Rules**
- Unit tests **never** call the real API or a real database — mock the boundary.
  Tests that depend on the network are flaky and will be ignored.
- Every bug fixed gets a test that reproduces it first.
- Coverage is a diagnostic, not a target; chasing a percentage produces tests
  that assert nothing.

## 13. CI/CD pipeline

Runs on every pull request; **must be green to merge**.

| Stage | Checks |
|---|---|
| **Lint** | Python linter/formatter check (e.g. ruff); SQL linter (e.g. sqlfluff) |
| **Unit tests** | `pytest` — no network access required |
| **dbt validation** | `dbt deps`, then `dbt build` against an isolated CI schema |
| **Docs** | `dbt docs generate` succeeds (catches broken refs and descriptions) |

**Notes**
- `dbt build` needs a database. Either point CI at a dedicated CI schema in the
  warehouse, or spin up a throwaway Postgres service container in the workflow.
  If neither is available, fall back to `dbt parse` / `dbt compile` so at least
  references and syntax are validated.
- CI must read credentials from repository secrets — never from committed files.
- Keep CI fast enough that it is not routinely bypassed; a slow pipeline is a
  disabled pipeline.
