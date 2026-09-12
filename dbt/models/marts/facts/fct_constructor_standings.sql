-- Grain: one row per (season, round, constructor).
--
-- The constructors' championship after each round — twin of
-- `fct_driver_standings`, and the second half of why PRD §6 split standings by
-- entity rather than keying one fact on "sometimes a driver, sometimes a
-- constructor". A polymorphic key there would need a nullable foreign key and
-- would break clean `relationships` tests on both sides.
--
-- **A periodic snapshot**, so `points` is a running total and **not additive
-- across rounds**. The snapshot key is `round`, already in the natural key.
--
-- Simpler than its twin in exactly one way: `Constructor` is a single object
-- rather than a list, so there is no cardinality to preserve.
--
-- This header used to claim a second way — that `championship_position` is
-- present on every row, unlike driver standings. That was checked, and true of
-- the 2024-2026 data it was checked against. The historical backfill made it
-- false: 3,107 null rows, most of them pre-1990, plus McLaren's 2007
-- exclusion. Two tables looking alike is indeed when a shared assumption goes
-- wrong — the assumption here was just the opposite one.

with standings as (

    select * from {{ ref('stg_constructor_standings') }}

),

constructors as (
    select constructor_id, constructor_key from {{ ref('dim_constructor') }}
),

races as (
    select season, round, race_key from {{ ref('dim_race') }}
),

joined as (

    select
        coalesce(races.race_key, {{ unknown_key() }})               as race_key,
        coalesce(constructors.constructor_key, {{ unknown_key() }}) as constructor_key,

        standings.season,
        standings.round,

        -- Running total, not an increment.
        standings.points,
        standings.wins,

        -- Legitimately null across the early history; use the flags below.
        standings.championship_position,
        standings.position_text,
        standings.is_unranked,
        standings.is_excluded,

        standings.source_key,
        standings.ingested_at,
        standings.updated_at

    from standings
    left join constructors on constructors.constructor_id = standings.constructor_id
    left join races        on races.season = standings.season
                          and races.round  = standings.round

)

select * from joined
