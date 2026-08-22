-- Grain: one row per constructor.
--
-- Reference data, complete for all of F1 history (214 rows).
--
-- **Type 1 downstream, with no SCD2.** Phase 0 walked every constructor list
-- from 1996 to 2024 and found no `constructor_id` ever carrying two names, so
-- rebrands are separate entities upstream rather than a mutated attribute:
-- `toro_rosso` (2006-19), `alphatauri` (2020-23) and `rb` (2024) are three
-- rows here, not one row with three versions. ARCHITECTURE decision 18.
--
-- The source's bare `name` is renamed to `constructor_name` per CONVENTIONS —
-- a column called `name` is ambiguous the moment it is joined to anything else
-- carrying one.

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
