-- Grain: one row per (season, round, constructor).
--
-- The constructors' championship after each round — the counterpart to
-- stg_driver_standings, and the reason PRD §6 splits standings into two facts
-- rather than one keyed on "sometimes a driver, sometimes a constructor". A
-- polymorphic key would need a nullable foreign key and would break clean
-- `relationships` tests on both sides.
--
-- `Constructor` is a single object rather than the driver standings' list, so
-- this model has no cardinality to preserve. That is the only way it is simpler
-- than its twin.
--
-- **It is not exempt from the missing-position quirk.** This header used to
-- claim it was — "no missing positions, no '-' marker" — checked against
-- 2024-2026 and true there: 1 null row in the whole 2020s. Across 77 seasons
-- `championship_position` is null on 3,107 rows, most of the early history:
--
--     decade   rows   null          3,090 carry positionText '-'
--       1950    163     72          17 carry 'E'
--       1970  2,114    739
--       1990  2,168    715
--       2010  2,152     16
--       2020  1,453      1
--
-- The 'E' rows are McLaren, 2007, every round — excluded from the
-- Constructors' Championship. They raced, scored nothing, and hold no position.
--
-- The lesson is the one Phase 0 already taught about `FastestLap` and `Time`:
-- the modern era is unrepresentative *by construction*. The sport grew more
-- regular over time, so recent seasons are the least likely to show an edge
-- case, and sampling them proves the least.

with source as (

    select * from {{ source('raw', 'constructor_standings') }}

),

typed as (

    select
        season::int    as season,
        round::int     as round,
        constructor_id,

        -- Legitimately null; see the header. Deliberately untested for null.
        (payload ->> 'position')::int  as championship_position,
        payload ->> 'positionText'     as position_text,

        (payload ->> 'points')::numeric as points,
        (payload ->> 'wins')::int       as wins,

        source_key,
        ingested_at,
        updated_at

    from source

),

flagged as (

    select
        *,

        -- Mirrors stg_driver_standings. Saves every consumer re-deriving the
        -- same null check, and makes the two twins answer the question the
        -- same way.
        championship_position is null as is_unranked,

        -- Excluded from the championship, as distinct from merely unranked.
        -- Only McLaren 2007 so far, but the marker is the source's, not ours.
        position_text = 'E' as is_excluded

    from typed

)

select * from flagged
