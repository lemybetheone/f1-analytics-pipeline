-- Grain: one row per (season, round, driver, stop).
--
-- `stop` is load-bearing in the grain, not a defensive extra: 2,081 rows span
-- only 1,134 distinct (season, round, driver) combinations, so without it 947
-- rows — 45% — would collide and the upsert would keep one stop per driver per
-- race while reporting success.
--
-- Two source quirks shape the SQL:
--
--   1. **`duration` is not always a number.** Most stops read '25.208', but 83
--      of them carry a minutes component — '1:01.930', and under a red flag as
--      long as '39:54.053'. A plain `::numeric` cast fails outright on those
--      (`invalid input syntax for type numeric: "1:14.773"`), so the raw text
--      is kept and a seconds value derived alongside it.
--
--   2. **`time` is a wall clock, not a duration and not a timestamp.** It reads
--      '18:05:33' — the local time of day the stop happened, with no date and
--      no zone. CONVENTIONS require timestamps in UTC; this one cannot be made
--      UTC without the circuit's offset, which the source does not provide. It
--      stays text rather than being cast into something that looks like a
--      timestamp but is not one.

with source as (

    select * from {{ source('raw', 'pitstops') }}

),

typed as (

    select
        season::int    as season,
        round::int     as round,
        driver_id,

        -- Part of the grain. Which stop of the race this was, 1-based.
        stop::int      as stop_number,

        (payload ->> 'lap')::int as lap,

        -- Kept verbatim so nothing is lost to the conversion below. '' is
        -- normalised to NULL: two stops are recorded with no timing at all,
        -- and an empty string is not a duration.
        nullif(payload ->> 'duration', '') as duration_display,

        -- Local wall-clock time of day. See note 2.
        payload ->> 'time'       as stop_time_local,

        source_key,
        ingested_at,
        updated_at

    from source

),

derived as (

    select
        *,

        -- 'mm:ss.sss' for long stops, plain seconds otherwise. Handling both
        -- here means no consumer has to know the format varies.
        --
        -- The null branch is not defensive padding: a `not_null` test on this
        -- column failed on the first build with a database error, because two
        -- stops carry an empty duration and ''::numeric raises rather than
        -- returning null. Null in, null out.
        case
            when duration_display is null then null
            when duration_display ~ '^[0-9]+:[0-9.]+$'
            then split_part(duration_display, ':', 1)::numeric * 60
               + split_part(duration_display, ':', 2)::numeric
            else duration_display::numeric
        end as duration_seconds

    from typed

)

select * from derived
