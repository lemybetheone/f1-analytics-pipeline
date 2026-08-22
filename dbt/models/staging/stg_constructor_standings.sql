-- Grain: one row per (season, round, constructor).
--
-- The constructors' championship after each round — the counterpart to
-- stg_driver_standings, and the reason PRD §6 splits standings into two facts
-- rather than one keyed on "sometimes a driver, sometimes a constructor". A
-- polymorphic key would need a nullable foreign key and would break clean
-- `relationships` tests on both sides.
--
-- The cleanest payload in the project: five keys, all present on all 601 rows,
-- `positionText` numeric throughout. It notably does **not** share the driver
-- standings' quirks — no missing positions, no '-' marker, and `Constructor`
-- is a single object rather than a list. Checked rather than assumed, because
-- the two tables looking alike is exactly when a shared assumption goes wrong.

with source as (

    select * from {{ source('raw', 'constructor_standings') }}

),

typed as (

    select
        season::int    as season,
        round::int     as round,
        constructor_id,

        (payload ->> 'position')::int  as championship_position,
        payload ->> 'positionText'     as position_text,

        (payload ->> 'points')::numeric as points,
        (payload ->> 'wins')::int       as wins,

        source_key,
        ingested_at,
        updated_at

    from source

)

select * from typed
