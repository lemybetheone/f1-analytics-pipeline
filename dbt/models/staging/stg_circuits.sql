with source as (

    select * from {{ source('raw', 'circuits') }}

),

renamed as (

    select
        (payload -> 'Location' ->> 'lat')::float as latitude,
        (payload -> 'Location' ->> 'long')::float as longitude,
        payload -> 'Location' ->> 'country' as country,
        payload -> 'Location' ->> 'locality' as locality,
        circuit_id,
        payload ->> 'circuitName' as circuit_name,
        payload ->> 'url'                 as wikipedia_url,

        source_key,
        ingested_at,
        updated_at
   from source

)

select * from renamed
