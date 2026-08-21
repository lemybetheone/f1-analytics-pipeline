-- Grain: one row per (season, round).
--
-- The event dimension's source. Three things in this payload are worth knowing
-- before reading the SQL, all measured across all 1,172 races:
--
--   1. `date` is present 100% of the time, but `time` only **37.6%** — races
--      before the mid-2000s have no recorded start time. So a naive
--      `(date || ' ' || time)::timestamptz` produces NULL for two thirds of
--      the table, silently.
--
--   2. Session blocks (practice, qualifying) exist for ~35% of races, and only
--      on modern weekends. They are legitimately absent, never "missing".
--
--   3. **The sprint qualifying session has been renamed twice.** The source
--      carries `SprintQualifying` (18 races), `SprintShootout` (6) and, for
--      the earliest sprint weekends, only a `Sprint` block (30). Reading one
--      key would silently drop the other formats — the kind of gap that looks
--      like "sprints did not exist yet" rather than "we read the wrong field".

with source as (

    select * from {{ source('raw', 'races') }}

),

renamed as (

    select
        -- Composite natural key. Both are text in the source; cast so that
        -- `round` sorts as 2 < 10 rather than '10' < '2'.
        season::int                       as season,
        round::int                        as round,

        payload ->> 'raceName'            as race_name,

        -- Only the foreign key is lifted from the nested Circuit object. The
        -- circuit's own attributes belong to stg_circuits — copying them here
        -- would mean two places to fix when a circuit is corrected upstream.
        payload -> 'Circuit' ->> 'circuitId' as circuit_id,

        (payload ->> 'date')::date        as race_date,

        -- Present for only 37.6% of races. Built defensively for that reason:
        -- consumers get a real UTC timestamp where one exists and NULL where
        -- the source never recorded a start time.
        case
            when payload ->> 'time' is not null
            then ((payload ->> 'date') || ' ' || (payload ->> 'time'))::timestamptz
        end                               as race_start_utc,

        -- Session schedule. All legitimately null for older races.
        {{ session_timestamp('payload', 'FirstPractice') }}  as practice_1_utc,
        {{ session_timestamp('payload', 'SecondPractice') }} as practice_2_utc,
        {{ session_timestamp('payload', 'ThirdPractice') }}  as practice_3_utc,
        {{ session_timestamp('payload', 'Qualifying') }}     as qualifying_utc,
        {{ session_timestamp('payload', 'Sprint') }}         as sprint_utc,

        -- The rename, handled once here so no downstream model has to know
        -- the sport changed the name of this session between seasons.
        coalesce(
            {{ session_timestamp('payload', 'SprintQualifying') }},
            {{ session_timestamp('payload', 'SprintShootout') }}
        )                                 as sprint_qualifying_utc,

        -- Derived flag rather than making every consumer test for a nested
        -- key. Keyed off the Sprint block, which is present for every sprint
        -- weekend regardless of what the qualifying session was called.
        (payload -> 'Sprint' is not null) as is_sprint_weekend,

        payload ->> 'url'                 as wikipedia_url,

        source_key,
        ingested_at,
        updated_at

    from source

)

select * from renamed
