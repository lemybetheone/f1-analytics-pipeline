with source as (

    select * from {{ source('raw', 'status') }}

),

renamed as (

    select
        status_id::int                    as status_id,
        payload ->> 'status'              as status,
        (payload ->> 'count')::int        as source_all_time_count,

        source_key,
        ingested_at,
        updated_at
    from source

)

select * from renamed
