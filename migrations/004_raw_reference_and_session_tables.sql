-- 004 — raw tables for the remaining reference and session-fact endpoints
--
-- Every grain below is the one measured in Phase 0 and recorded in SCHEMA §2.
-- All of these upsert: reference data is correctable upstream, and session
-- results are adjudicated and amended after publication.
--
-- The shape is uniform on purpose — grain key columns, untouched payload,
-- lineage back to the lake object, and the ingested/updated split that makes a
-- post-publication amendment visible. Uniformity is what lets one generic
-- loader serve every endpoint instead of one module each.

-- --- reference data --------------------------------------------------------

create table if not exists raw.seasons (
    season      text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_seasons_pkey primary key (season)
);

create table if not exists raw.circuits (
    circuit_id  text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_circuits_pkey primary key (circuit_id)
);

create table if not exists raw.drivers (
    driver_id   text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_drivers_pkey primary key (driver_id)
);

-- Type 1 in the mart. Phase 0 found no constructorId ever carrying two names:
-- rebrands are separate ids upstream, so there is no attribute for SCD2 to
-- track (ARCHITECTURE decision 18).
create table if not exists raw.constructors (
    constructor_id text        not null,
    payload        jsonb       not null,
    source_key     text        not null,
    ingested_at    timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    constraint raw_constructors_pkey primary key (constructor_id)
);

-- 136 distinct values — too many for an accepted_values test, which is why
-- SCHEMA §4 proposes grouping them into dim_status rather than enumerating.
create table if not exists raw.status (
    status_id   text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_status_pkey primary key (status_id)
);

-- Grain (season, round). Mutable until the race is run: session dates shift
-- during a live season, so insert-do-nothing would freeze a stale schedule.
-- Sprint and second-practice blocks are absent on non-sprint weekends, which is
-- why nothing beyond the grain keys is promoted out of the payload.
create table if not exists raw.races (
    season      text        not null,
    round       text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_races_pkey primary key (season, round)
);

-- --- session facts ---------------------------------------------------------

-- Q1 is present for all, Q2 for 76%, Q3 for 51% — qualifying elimination, not
-- missing data. Those live in the payload precisely because they are
-- legitimately absent and must never carry a not_null test.
create table if not exists raw.qualifying (
    season      text        not null,
    round       text        not null,
    driver_id   text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_qualifying_pkey primary key (season, round, driver_id)
);

-- Only some weekends have a sprint. An empty response for a non-sprint round is
-- an empty result set, not a failure.
create table if not exists raw.sprint (
    season      text        not null,
    round       text        not null,
    driver_id   text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_sprint_pkey primary key (season, round, driver_id)
);

comment on table raw.seasons      is 'Grain: season. Upserted reference data.';
comment on table raw.circuits     is 'Grain: circuit_id. Upserted reference data.';
comment on table raw.drivers      is 'Grain: driver_id. Upserted reference data.';
comment on table raw.constructors is 'Grain: constructor_id. Type 1 downstream - rebrands are separate ids upstream.';
comment on table raw.status       is 'Grain: status_id. 136 values; grouped into dim_status downstream.';
comment on table raw.races        is 'Grain: (season, round). Mutable until run - schedules shift mid-season.';
comment on table raw.qualifying   is 'Grain: (season, round, driver). Q2/Q3 legitimately absent - elimination, not missing data.';
comment on table raw.sprint       is 'Grain: (season, round, driver). Only sprint weekends produce rows.';
