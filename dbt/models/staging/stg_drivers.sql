-- Grain: one row per driver.
--
-- Staging's whole job (SCHEMA §3): rename vague source fields, cast types, and
-- convert dates — no joins, no aggregation, no filtering. Everything arrives
-- from the API as text, including numbers and dates, so the casting here is not
-- tidying: it is the layer where the data first acquires real types.

with source as (

    select * from {{ source('raw', 'drivers') }}

),

renamed as (

    select
        driver_id,

        -- `code` is the three-letter TV abbreviation (VER, HAM); `permanent_number`
        -- is the driver's chosen career number. Both are absent for older
        -- drivers — permanent numbers only exist from 2014 — so neither is
        -- eligible for a not_null test, and neither belongs in a key.
        payload ->> 'code'            as driver_code,
        payload ->> 'givenName'       as first_name,
        payload ->> 'familyName'      as last_name,

        -- Built here rather than in the mart so every consumer spells a
        -- driver's name the same way.
        (payload ->> 'givenName') || ' ' || (payload ->> 'familyName')
                                      as full_name,

        payload ->> 'nationality'     as nationality,

        -- ISO date string in the payload; a real date from here down.
        (payload ->> 'dateOfBirth')::date as date_of_birth,

        -- Numeric-looking but genuinely optional, so cast defensively rather
        -- than with ::int, which would fail the whole model on one bad row.
        nullif(payload ->> 'permanentNumber', '')::int as permanent_number,

        payload ->> 'url'             as wikipedia_url,

        -- Lineage and load metadata, carried through so a mart row can be
        -- traced back to the lake object it came from.
        source_key,
        ingested_at,
        updated_at

    from source

)

select * from renamed
