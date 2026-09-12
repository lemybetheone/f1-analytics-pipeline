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
-- Simpler than its twin in two ways, both verified rather than assumed:
-- `championship_position` is present on every row here (no '-' marker, unlike
-- driver standings), and `Constructor` is a single object rather than a list.
-- Two tables looking alike is exactly when a shared assumption goes wrong.

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

        standings.championship_position,
        standings.position_text,

        standings.source_key,
        standings.ingested_at,
        standings.updated_at

    from standings
    left join constructors on constructors.constructor_id = standings.constructor_id
    left join races        on races.season = standings.season
                          and races.round  = standings.round

)

select * from joined
