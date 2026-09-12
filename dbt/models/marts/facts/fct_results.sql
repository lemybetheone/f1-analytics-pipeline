-- Grain: one row per driver per race. **The core fact.**
--
-- PRD §6 themes 2, 3, 4 and 6 all resolve here. Transaction-grained: one row
-- per driver per race, which is the event the sport actually produces.
--
-- **Both driver_key and constructor_key sit on this fact**, and that is the
-- decision that makes theme 4 work. A driver's team changes within a season, so
-- it cannot live on `dim_driver` — but at *race* grain it is single-valued.
-- Teammate head-to-head therefore becomes a self-join on (race_key,
-- constructor_key) with different driver_keys, rather than a bridge table.
--
-- **Foreign keys are left-joined and coalesced to the Unknown member**, per
-- SCHEMA §5. An inner join would be tempting — Phase 0 measured coverage at
-- 100% and all four still resolve today — but a fact row dropped by an inner
-- join is invisible: the total simply comes out lower and nothing errors.
-- Routing to Unknown keeps the row and makes the drift detectable.
--
-- That choice has a consequence worth naming: it makes the `relationships`
-- tests **vacuous**, because a coalesced key always resolves. The singular test
-- `assert_no_facts_on_unknown_members` is what actually detects drift now. The
-- pair is deliberate — `relationships` proves the key resolves, the singular
-- test proves it resolved to something real.
--
-- **season and round are carried on the fact**, which is a denormalisation.
-- The line drawn: denormalise what is filtered on constantly, not what is
-- joined for. Almost every query slices by season, and forcing a dim_race join
-- for that is friction with no benefit. Driver, constructor and status names
-- are *not* carried, because those are joined for rather than filtered on, and
-- duplicating them invites two sources of truth for a name.

with results as (

    select * from {{ ref('stg_results') }}

),

drivers as (
    select driver_id, driver_key from {{ ref('dim_driver') }}
),

constructors as (
    select constructor_id, constructor_key from {{ ref('dim_constructor') }}
),

races as (
    select season, round, race_key from {{ ref('dim_race') }}
),

statuses as (
    select status, status_key from {{ ref('dim_status') }}
),

joined as (

    select
        -- Foreign keys. coalesce, not an inner join — see the header.
        coalesce(races.race_key, {{ unknown_key() }})              as race_key,
        coalesce(drivers.driver_key, {{ unknown_key() }})          as driver_key,
        coalesce(constructors.constructor_key, {{ unknown_key() }}) as constructor_key,
        coalesce(statuses.status_key, {{ unknown_key() }})         as status_key,

        -- Denormalised for filtering, not for joining.
        results.season,
        results.round,

        -- Degenerate dimensions: attributes of the event itself with no
        -- dimension of their own.
        results.car_number,
        results.position_text,

        -- Measures.
        results.grid_position,
        results.classified_position,
        results.points,
        results.laps_completed,
        results.positions_gained,

        results.race_time_millis,
        results.race_time_display,

        results.fastest_lap_number,
        results.fastest_lap_rank,
        results.fastest_lap_time_display,
        results.fastest_lap_avg_speed_kph,

        -- Outcome flags. `is_classified` is the authoritative answer to "did
        -- this driver finish" — it reads positionText, the stewards' marker.
        -- Do not substitute dim_status.status_implies_running_at_end: the two
        -- disagree on 20 of 1,244 rows and answer different questions.
        results.is_classified,
        results.is_retired,
        results.is_disqualified,
        results.is_withdrawn,

        -- Lineage, carried through from staging so a fact row can be traced to
        -- the lake object it came from.
        results.source_key,
        results.ingested_at,
        results.updated_at

    from results
    left join drivers      on drivers.driver_id           = results.driver_id
    left join constructors on constructors.constructor_id = results.constructor_id
    left join races        on races.season = results.season
                          and races.round  = results.round
    left join statuses     on statuses.status             = results.status

)

select * from joined
