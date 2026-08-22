-- Grain: one row per championship season (77 rows, 1950-2026).
--
-- The thinnest model in the project: the source carries only the year and a
-- reference link. It exists because SCHEMA §3 specifies one staging model per
-- raw source, and because it is the authoritative list of seasons — a season
-- present in `stg_races` but absent here would mean the reference load had
-- drifted from the race schedule.
--
-- `season` is cast to int to match `stg_races` and `stg_results`. Left as text
-- it cannot be joined to them at all: Postgres rejects `text = integer` rather
-- than coercing.

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
