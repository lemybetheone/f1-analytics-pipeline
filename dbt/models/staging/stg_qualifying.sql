-- Grain: one row per (season, round, driver).
--
-- Breaks the pattern the other session models share: no points, no status, no
-- positionText, no Time or FastestLap block. Qualifying has its own shape.
--
-- Two things measured from the payload drive the SQL below.
--
--   1. **Absence comes in two forms, and they mean different things.** A
--      missing Q2 key means the driver was eliminated in Q1 and never reached
--      that session. A Q2 key holding an empty string means they *did* reach
--      it and set no time — crashed, or did not run. Across the loaded
--      seasons: Q2 missing on 298 rows, empty on 30. Treating them alike would
--      count a non-participant as a participant, so `reached_*` reads the key
--      and the time columns normalise '' to NULL.
--
--   2. **The times are not numbers.** '1:29.179' is minutes:seconds.thousandths
--      and casts to nothing useful. They stay text here. Converting to
--      milliseconds is an interpretation, and staging translates rather than
--      interprets (SCHEMA §3) — the mart can do it once, where the choice is
--      visible.

with source as (

    select * from {{ source('raw', 'qualifying') }}

),

typed as (

    select
        season::int    as season,
        round::int     as round,
        driver_id,

        payload -> 'Constructor' ->> 'constructorId' as constructor_id,

        (payload ->> 'number')::int   as car_number,

        -- **Qualifying** position, which is not the same as the grid position
        -- on stg_results: penalties move drivers between the two. PRD §6 theme
        -- 3 depends on the distinction, so the name says which one this is.
        (payload ->> 'position')::int as qualifying_position,

        -- '' normalised to NULL so "did they set a time" is a null check
        -- rather than a check for null-or-empty at every call site.
        nullif(payload ->> 'Q1', '')  as q1_time,
        nullif(payload ->> 'Q2', '')  as q2_time,
        nullif(payload ->> 'Q3', '')  as q3_time,

        -- Participation, read from the key rather than the value — see note 1.
        -- `?` is the jsonb key-existence operator.
        payload ? 'Q2'                as reached_q2,
        payload ? 'Q3'                as reached_q3,

        source_key,
        ingested_at,
        updated_at

    from source

),

derived as (

    select
        *,

        -- The time that actually set the grid slot: the last session in which
        -- the driver recorded one. Coalescing downward means a Q1-eliminated
        -- driver contributes their Q1 lap, which is the correct comparison
        -- when ranking a full field.
        coalesce(q3_time, q2_time, q1_time) as decisive_time,

        -- Which session they were knocked out in. Useful on its own, and it
        -- keeps every consumer from re-deriving the same three-way case.
        case
            when reached_q3 then 'Q3'
            when reached_q2 then 'Q2'
            else 'Q1'
        end as eliminated_in,

        -- Set no lap at all despite taking part. 33 rows in the loaded
        -- seasons — a real outcome, not missing data.
        q1_time is null as set_no_time

    from typed

)

select * from derived
