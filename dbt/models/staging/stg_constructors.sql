with source as (

    select * from {{ source('raw', 'constructors') }}

),

renamed as (

    select
        constructor_id,
        payload ->> 'name' as constructor_name,
        payload ->> 'nationality' as nationality,
        payload ->> 'url'                 as wikipedia_url,

        source_key,
        ingested_at,
        updated_at
   from source

)

select * from renamed
