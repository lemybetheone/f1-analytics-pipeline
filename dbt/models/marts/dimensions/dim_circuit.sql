-- Grain: one row per circuit, plus one Unknown member.
--
-- **Type 1.** Circuit attributes are corrected upstream rather than versioned —
-- a renamed or re-measured circuit overwrites in place, and Phase 0 found
-- nothing here that changes in a way worth keeping history for.
--
-- Coordinates are `double precision`, not `numeric`: that is what geospatial
-- tooling expects, and exact decimal precision is meaningless for a latitude.
-- The Unknown member casts them explicitly, since an untyped null in a UNION
-- would silently coerce the real column to text.
--
-- `country` and `locality` are left null on the Unknown member rather than
-- given placeholder text. That is deliberate — a fabricated country would join
-- to a real one in a rollup — but it means neither can carry a not_null test
-- here, even though both are non-null in stg_circuits.


with circuits as (

    select * from {{ ref('stg_circuits') }}

),

known as (

    select
        {{ dbt_utils.generate_surrogate_key(['circuit_id']) }} as circuit_key,
        circuit_id,
        circuit_name,
        latitude,
        longitude,
        country,
        locality,
        wikipedia_url,

        false as is_unknown

    from circuits

),

unknown_member as (


    select
        {{ unknown_key() }}                  as circuit_key,
        '-1'::text                           as circuit_id,
        'Unknown circuit'::text              as circuit_name,
        null::float                          as latitude,
        null::float                          as longitude,
        null::text                           as country,
        null::text                           as locality,
        null::text                           as wikipedia_url,
        true                                 as is_unknown

)

select * from known
union all
select * from unknown_member
