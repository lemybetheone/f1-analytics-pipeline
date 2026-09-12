-- Grain: one row per constructor, plus one Unknown member.
--
-- **Type 1 — and this is the dimension where that was hard-won.** PRD §6
-- originally locked this as SCD Type 2, to make Toro Rosso → AlphaTauri → RB
-- queryable as one team's identity history. Phase 0 then walked every
-- constructor list from 1996 to 2024 and found *no* constructor_id ever
-- carrying two names: the source models each rebrand as a distinct id with a
-- contiguous, non-overlapping span. With no attribute changing in place, Type 2
-- would produce valid_from/valid_to/is_current columns over single-version
-- rows. The decision was reversed (ARCHITECTURE decision 18).
--
-- **Consequence, accepted:** rebrands are separate constructors here, exactly
-- as upstream. "How has this team performed across all its names" is not
-- answerable without a hand-curated lineage mapping, which was considered and
-- rejected as scope.

with constructors as (

    select * from {{ ref('stg_constructors') }}

),

known as (

    select
        {{ dbt_utils.generate_surrogate_key(['constructor_id']) }} as constructor_key,
        constructor_id,
        constructor_name,
        nationality,
        wikipedia_url,

        false as is_unknown

    from constructors

),

unknown_member as (


    select
        {{ unknown_key() }}                  as constructor_key,
        '-1'::text                           as constructor_id,
        'Unknown constructor'::text          as constructor_name,
        null::text                           as nationality,
        null::text                           as wikipedia_url,
        true                                 as is_unknown

)

select * from known
union all
select * from unknown_member
