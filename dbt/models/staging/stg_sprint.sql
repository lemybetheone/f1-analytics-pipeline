with source as (

    select * from {{ source('raw', 'sprint') }}

),

typed as (

    select
        season::int    as season,
        round::int     as round,
        driver_id,
        payload -> 'Constructor' ->> 'constructorId' as constructor_id,

        (payload ->> 'number')::int    as car_number,
        (payload ->> 'grid')::int      as grid_position,
        (payload ->> 'position')::int  as classified_position,
        payload ->> 'positionText'     as position_text,
        (payload ->> 'points')::numeric as points,

        (payload ->> 'laps')::int      as laps_completed,
        payload ->> 'status'           as status,

        (payload -> 'Time' ->> 'millis')::bigint as race_time_millis,
        payload -> 'Time' ->> 'time'             as race_time_display,

        (payload -> 'FastestLap' ->> 'lap')::int  as fastest_lap_number,
        (payload -> 'FastestLap' ->> 'rank')::int as fastest_lap_rank,
        payload -> 'FastestLap' -> 'Time' ->> 'time' as fastest_lap_time_display,

        source_key,
        ingested_at,
        updated_at

    from source

),

derived as (

    select
        *,
        position_text ~ '^[0-9]+$' as is_classified,
        position_text = 'R'        as is_retired,
        position_text = 'D'        as is_disqualified,
        position_text = 'W'        as is_withdrawn,
        case
            when position_text ~ '^[0-9]+$' and grid_position > 0
            then grid_position - classified_position
        end as positions_gained

    from typed

)

select * from derived
