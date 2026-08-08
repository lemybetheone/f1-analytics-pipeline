-- 005 — raw tables for the race-scoped endpoints
--
-- These three are fetched one round at a time. Pit stops return HTTP 400
-- without a round; season-scoped standings return only the final round while
-- reporting a total that looks like every round.

-- Grain (season, round, driver, stop). The `stop` column is what makes this
-- grain correct: a driver pits more than once in a race, so (season, round,
-- driver) would collide and silently keep only the last pit stop. Phase 0
-- measured 43 pit stops across 20 drivers in one 2024 race — roughly two each.
create table if not exists raw.pitstops (
    season      text        not null,
    round       text        not null,
    driver_id   text        not null,
    stop        text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_pitstops_pkey primary key (season, round, driver_id, stop)
);

-- Periodic snapshot at (season, round, driver). The snapshot key is `round`,
-- already part of the natural key, which is why no `snapshot_date` column is
-- needed here — see SCHEMA §2. Upserted because a stewards' decision that
-- amends a result also amends the standings that follow from it.
create table if not exists raw.driver_standings (
    season      text        not null,
    round       text        not null,
    driver_id   text        not null,
    payload     jsonb       not null,
    source_key  text        not null,
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    constraint raw_driver_standings_pkey primary key (season, round, driver_id)
);

-- The payload here carries a *list* of constructors per driver-standing row:
-- a driver who changes team mid-season has several. That is why the
-- driver→constructor relationship lives on the results fact at race grain
-- rather than on this table (SCHEMA §2, finding 5).
create table if not exists raw.constructor_standings (
    season         text        not null,
    round          text        not null,
    constructor_id text        not null,
    payload        jsonb       not null,
    source_key     text        not null,
    ingested_at    timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    constraint raw_constructor_standings_pkey primary key (season, round, constructor_id)
);

comment on table raw.pitstops is
    'Grain: (season, round, driver, stop). `stop` is in the key - drivers pit more than once.';
comment on table raw.driver_standings is
    'Grain: (season, round, driver). Periodic snapshot; the snapshot key is `round`.';
comment on table raw.constructor_standings is
    'Grain: (season, round, constructor). Periodic snapshot; the snapshot key is `round`.';
