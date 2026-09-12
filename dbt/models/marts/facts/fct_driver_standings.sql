-- Grain: one row per (season, round, driver).
--
-- **A periodic snapshot fact**: the drivers' championship table as it stood
-- after each round. Unlike a transaction fact, each row restates a running
-- total rather than recording a new event — so `points` here is **not
-- additive across rounds**. Summing it over a season would count every point
-- once per subsequent round.
--
-- The snapshot key is `round`, already part of the natural key, which is why
-- this needs no `snapshot_date` column (SCHEMA §2). That is the whole reason
-- no source in this project required an append-only snapshot table.
--
-- **Split by entity, not polymorphic.** PRD §6 locked this: a single standings
-- fact keyed on "sometimes a driver, sometimes a constructor" would need a
-- nullable foreign key and would break clean `relationships` tests on both
-- sides. `fct_constructor_standings` is its twin.
--
-- **The driver's constructor is deliberately absent.** The source carries a
-- *list* here — 62 rows have two, because a driver who changes team mid-season
-- has raced for both. Flattening it would silently pick one. The single-valued
-- relationship lives on `fct_results` at race grain instead.

with standings as (

    select * from {{ ref('stg_driver_standings') }}

),

drivers as (
    select driver_id, driver_key from {{ ref('dim_driver') }}
),

races as (
    select season, round, race_key from {{ ref('dim_race') }}
),

joined as (

    select
        coalesce(races.race_key, {{ unknown_key() }})     as race_key,
        coalesce(drivers.driver_key, {{ unknown_key() }}) as driver_key,

        standings.season,
        standings.round,

        -- Non-additive across rounds: a running total, not an increment.
        standings.points,
        standings.wins,

        -- Legitimately null where the source declines to rank an entirely tied
        -- field — 8 rows, all drivers on zero points early in a season.
        standings.championship_position,
        standings.position_text,
        standings.is_unranked,

        -- Cardinality preserved rather than flattened: how many constructors
        -- the driver has raced for this season, and which.
        standings.constructor_count,
        standings.constructor_ids,
        standings.changed_constructor_in_season,

        standings.source_key,
        standings.ingested_at,
        standings.updated_at

    from standings
    left join drivers on drivers.driver_id = standings.driver_id
    left join races   on races.season = standings.season
                     and races.round  = standings.round

)

select * from joined
