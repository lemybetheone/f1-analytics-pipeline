-- Grain: one row per driver, plus one Unknown member.
--
-- **Type 1.** Attributes are overwritten in place, no history kept. That is a
-- measured decision, not a default: Phase 0 found nothing in this source that
-- mutates an attribute over time, and the one attribute that looked like it
-- did — constructor identity — turned out to be separate ids upstream
-- (ARCHITECTURE decisions 18 and 19).
--
-- **A driver's team is deliberately not here.** It changes within a season —
-- 62 standings rows carry two constructors, Bearman drove for Ferrari and Haas
-- across 2024 — so it belongs on `fct_results` at race grain, where it is
-- genuinely single-valued. Putting it on the dimension would force a choice
-- between "first team", "last team" or a fabricated one, and all three are
-- wrong. This is also what makes teammate head-to-head (PRD §6 theme 4) a
-- self-join on the fact rather than a separate bridge.
--
-- Two things the marts layer introduces that staging did not need:
--
--   1. **A surrogate key.** Facts join on `driver_key`, a hash, rather than on
--      the source's `driver_id`. That decouples the warehouse from source
--      quirks: if Jolpica ever renamed an id, only this model changes, and
--      every fact keeps its join. The natural key is kept alongside it for
--      traceability and for reconciling against raw.
--
--   2. **An Unknown member.** See macros/unknown_key.sql for why.

with drivers as (

    select * from {{ ref('stg_drivers') }}

),

known as (

    select
        {{ dbt_utils.generate_surrogate_key(['driver_id']) }} as driver_key,

        -- Natural key retained. SCHEMA §5 wants facts joining on the surrogate,
        -- but losing the source id would make it impossible to trace a mart row
        -- back to raw, or to reconcile counts against the API.
        driver_id,

        driver_code,
        first_name,
        last_name,
        full_name,
        nationality,
        date_of_birth,
        permanent_number,
        wikipedia_url,

        false as is_unknown

    from drivers

),

unknown_member as (

    -- Every column is cast explicitly. A UNION infers its types from the first
    -- branch, so an untyped `null` here would either fail or silently coerce
    -- the real column to text — and a dimension whose date_of_birth became text
    -- would break every downstream date comparison without erroring.
    select
        {{ unknown_key() }}                  as driver_key,
        '-1'::text                           as driver_id,
        null::text                           as driver_code,
        null::text                           as first_name,
        null::text                           as last_name,
        'Unknown driver'::text               as full_name,
        null::text                           as nationality,
        null::date                           as date_of_birth,
        null::int                            as permanent_number,
        null::text                           as wikipedia_url,
        true                                 as is_unknown

)

select * from known
union all
select * from unknown_member
