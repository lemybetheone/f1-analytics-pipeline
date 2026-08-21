-- Grain: one row per (season, round, driver).
--
-- The core fact source — PRD §6 themes 2, 3, 4 and 6 all resolve to this table.
-- Five things measured from the real payload shape the SQL below:
--
--   1. `position` is populated for **every** row including retirements: a
--      driver who crashed on lap 5 still has a position. It is a *classified
--      order*, not a finishing position. `positionText` is what distinguishes
--      them, and it carries three markers, not one — R (retired, 130), W
--      (withdrew, 13), D (disqualified, 7).
--
--   2. `points` is cast to **numeric, not int**. F1 awards half points when a
--      race is stopped early — 2021 Belgium, several 1950s races. None appear
--      in the three seasons currently loaded, so `::int` would work today and
--      fail the moment the historical backfill runs. Model for the data that
--      will be there.
--
--   3. `status` has 5 distinct values in the loaded seasons and **136** in the
--      source's own status list. An `accepted_values` test written against
--      today's data would break on backfill, which is why the grouping lives
--      in dim_status instead (SCHEMA §4).
--
--   4. `grid` of 0 means a pit lane start, not "unknown" — one such row exists
--      already. Kept as 0 rather than nulled, because it is a real value.
--
--   5. Nested blocks are absent at two different depths: `FastestLap` on 4% of
--      rows, and `FastestLap.AverageSpeed` on 64% of them. Phase 0 measured
--      `FastestLap` as entirely absent before 2000, so these get sparser, not
--      denser, once history loads.

with source as (

    select * from {{ source('raw', 'results') }}

),

typed as (

    select
        -- Grain keys come from the promoted columns, which the primary key
        -- already guarantees, rather than being dug back out of the payload.
        season::int    as season,
        round::int     as round,
        driver_id,

        -- Foreign key only. The driver's name and the constructor's name live
        -- in their own dimensions; copying them here would mean two places to
        -- correct when the source fixes a spelling.
        payload -> 'Constructor' ->> 'constructorId' as constructor_id,

        (payload ->> 'number')::int    as car_number,

        -- 0 is a pit lane start, and is meaningful rather than missing.
        (payload ->> 'grid')::int      as grid_position,

        -- Classified order. Non-null for retirements too — see note 1.
        (payload ->> 'position')::int  as classified_position,
        payload ->> 'positionText'     as position_text,

        -- numeric, not int — see note 2.
        (payload ->> 'points')::numeric as points,

        (payload ->> 'laps')::int      as laps_completed,
        payload ->> 'status'           as status,

        -- Total race time, winners and classified finishers only.
        (payload -> 'Time' ->> 'millis')::bigint as race_time_millis,
        payload -> 'Time' ->> 'time'             as race_time_display,

        -- Fastest lap. Absent entirely before 2000.
        (payload -> 'FastestLap' ->> 'lap')::int  as fastest_lap_number,
        (payload -> 'FastestLap' ->> 'rank')::int as fastest_lap_rank,
        payload -> 'FastestLap' -> 'Time' ->> 'time' as fastest_lap_time_display,

        -- Every observed row reports kph, so the unit is in the column name
        -- rather than carried as a separate column nobody would filter on.
        (payload -> 'FastestLap' -> 'AverageSpeed' ->> 'speed')::numeric
                                       as fastest_lap_avg_speed_kph,

        source_key,
        ingested_at,
        updated_at

    from source

),

derived as (

    select
        *,

        -- Postgres cannot reference an alias defined in the same SELECT, which
        -- is why these live in a second CTE rather than beside the casts.

        -- The DNF question in PRD §6 theme 6 depends on this distinction, and
        -- filtering on `classified_position` instead would silently count
        -- every retirement as a finish.
        position_text ~ '^[0-9]+$' as is_classified,
        position_text = 'R'        as is_retired,
        position_text = 'D'        as is_disqualified,
        position_text = 'W'        as is_withdrawn,

        -- Theme 3: who gains places on race day. Only meaningful for a
        -- classified finish, and a pit lane start (grid 0) has no grid
        -- position to gain from, so both are excluded rather than producing a
        -- flattering number.
        case
            when position_text ~ '^[0-9]+$' and grid_position > 0
            then grid_position - classified_position
        end as positions_gained

    from typed

)

select * from derived
