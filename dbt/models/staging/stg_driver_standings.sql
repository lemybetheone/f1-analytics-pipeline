-- Grain: one row per (season, round, driver).
--
-- A periodic snapshot: the championship table as it stood after each round.
-- The snapshot key is `round`, which is already part of the natural key — which
-- is why this needs no `snapshot_date` column (SCHEMA §2).
--
-- Two things measured from the payload:
--
--   1. **`position` is null on 8 rows**, where `positionText` reads '-'. Those
--      are drivers on zero points and zero wins early in a season, before
--      anyone has separated them — 2025 round 1 has six. The source declines
--      to rank a field that is entirely tied rather than inventing an order.
--      So `position` carries no not_null test, and `positionText` is what is
--      always present.
--
--   2. **`Constructors` is a list, not an object.** 62 rows carry two: Oliver
--      Bearman drove for Ferrari and Haas across 2024. Flattening it to a
--      single constructor_id would silently pick one and imply a driver has
--      exactly one team per season, which is false.
--
--      It is deliberately not flattened here. The driver-to-constructor
--      relationship lives on `stg_results` at **race** grain, where it is
--      genuinely single-valued — that is the reason PRD §6 puts
--      `constructor_key` on the results fact rather than on standings.

with source as (

    select * from {{ source('raw', 'driver_standings') }}

),

typed as (

    select
        season::int    as season,
        round::int     as round,
        driver_id,

        -- Nullable — see note 1. Cast defensively rather than with ::int,
        -- which would fail on the absent values.
        nullif(payload ->> 'position', '')::int as championship_position,

        -- Always present, and carries '-' where position is absent.
        payload ->> 'positionText'  as position_text,

        -- numeric for the same reason as results: F1 awards half points for a
        -- shortened race, and none appear in the loaded seasons.
        (payload ->> 'points')::numeric as points,
        (payload ->> 'wins')::int      as wins,

        -- The list preserved rather than collapsed. `jsonb_array_length` is
        -- the honest cardinality; the ids are kept as an array so a consumer
        -- can unnest them deliberately instead of being handed a false 1:1.
        jsonb_array_length(payload -> 'Constructors') as constructor_count,
        array(
            select jsonb_array_elements(payload -> 'Constructors') ->> 'constructorId'
        ) as constructor_ids,

        source_key,
        ingested_at,
        updated_at

    from source

),

derived as (

    select
        *,

        -- True where the source declined to rank the driver — everyone tied on
        -- zero. Saves each consumer re-deriving the same '-' check.
        championship_position is null as is_unranked,

        -- A driver who changed team mid-season. 62 rows.
        constructor_count > 1 as changed_constructor_in_season

    from typed

)

select * from derived
