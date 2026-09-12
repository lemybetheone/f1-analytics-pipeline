-- Grain: one row per driver per sprint race.
--
-- **Why this is a separate fact rather than a column on `fct_results`.**
-- Building the standings facts surfaced that the points reconciliation PRD §6
-- commits to does not work from `fct_results` alone — 2024 official 437 for
-- Verstappen against 399 derived. Race plus sprint reconciles exactly: 24 of
-- 24 drivers, zero residual.
--
-- Unioning the two into one fact with a `session_type` discriminator was
-- tempting, and `stg_sprint` was deliberately given column names identical to
-- `stg_results` so it would be possible. It was rejected because it would make
-- **every existing query wrong by default**: finishing order, DNF rate and
-- positions gained would all include sprint rows unless the author remembered
-- `where session_type = 'race'`. A fact whose grain requires a filter to be
-- correct is a reliable source of wrong numbers. ARCHITECTURE decision 28.
--
-- Consequence accepted: "total championship points" is a union of two facts
-- rather than one sum. That union is explicit and lives in one place, where a
-- forgotten filter would be silent and live everywhere.
--
-- Structurally identical to `fct_results` except for one column: sprint
-- payloads carry no `FastestLap.AverageSpeed` at all — 0 of 328 rows — so
-- there is no average-speed measure here.

with sprint as (

    select * from {{ ref('stg_sprint') }}

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
        coalesce(races.race_key, {{ unknown_key() }})              as race_key,
        coalesce(drivers.driver_key, {{ unknown_key() }})          as driver_key,
        coalesce(constructors.constructor_key, {{ unknown_key() }}) as constructor_key,
        coalesce(statuses.status_key, {{ unknown_key() }})         as status_key,

        sprint.season,
        sprint.round,

        sprint.car_number,
        sprint.position_text,

        sprint.grid_position,
        sprint.classified_position,
        sprint.points,
        sprint.laps_completed,
        sprint.positions_gained,

        sprint.race_time_millis,
        sprint.race_time_display,

        sprint.fastest_lap_number,
        sprint.fastest_lap_rank,
        sprint.fastest_lap_time_display,

        sprint.is_classified,
        sprint.is_retired,
        sprint.is_disqualified,
        sprint.is_withdrawn,

        sprint.source_key,
        sprint.ingested_at,
        sprint.updated_at

    from sprint
    left join drivers      on drivers.driver_id           = sprint.driver_id
    left join constructors on constructors.constructor_id = sprint.constructor_id
    left join races        on races.season = sprint.season
                          and races.round  = sprint.round
    left join statuses     on statuses.status             = sprint.status

)

select * from joined
