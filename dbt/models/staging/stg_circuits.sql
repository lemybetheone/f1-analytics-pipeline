-- Grain: one row per circuit.
--
-- Reference data, complete for all of F1 history (78 rows). Attributes come
-- out of the nested `Location` object; `latitude` and `longitude` are cast to
-- double precision, the type geospatial tooling expects — `numeric` would buy
-- an exactness a coordinate does not have.
--
-- Named `latitude`/`longitude` rather than the source's `lat`/`long`:
-- CONVENTIONS forbids reserved words, and `long` is reserved or special in
-- several engines even though Postgres accepts it.
--
-- `country` is free text, not a controlled vocabulary — 'UK', 'USA' and 'UAE'
-- sit alongside 'South Africa'. Joining it to any external country reference
-- needs an explicit mapping.

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
