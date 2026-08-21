with source as (

    select * from {{ source('raw', 'seasons') }}

),

renamed as (

    select
        season::int    as season,
        payload ->> 'url'   as wikipedia_url,
        source_key,
        ingested_at,
        updated_at
    from source

)

select * from renamed
