-- A singular test: no fact row should have been routed to an Unknown member.
--
-- Why this exists, when `relationships` tests already guard every foreign key:
-- those tests became **vacuous** the moment facts started coalescing unmatched
-- keys to `unknown_key()`. The Unknown member is a real row in every
-- dimension, so a coalesced key always resolves and `relationships` can never
-- fail. The structural test still earns its place — it would catch a coalesce
-- being removed, or a key built from the wrong column — but it no longer
-- detects the thing it was originally there for.
--
-- This is that detector. Phase 0 measured foreign-key coverage at 100% across
-- all 26,115 historical result rows, so a row landing on Unknown means the
-- source has drifted: a driver or constructor appearing in results before the
-- reference load knows about it. That is worth failing a build over, not
-- warning about, because the alternative is a silently under-counted
-- dimension rollup.
--
-- The pair is the point: `relationships` proves the key *resolves*,
-- this proves it resolved to something *real*.

with unknown as (
    select {{ unknown_key() }} as key
),

offenders as (

    select
        'fct_results' as model,
        'driver_key'  as column_name,
        season,
        round,
        driver_key    as offending_key
    from {{ ref('fct_results') }}
    where driver_key = (select key from unknown)

    union all

    select 'fct_results', 'constructor_key', season, round, constructor_key
    from {{ ref('fct_results') }}
    where constructor_key = (select key from unknown)

    union all

    select 'fct_results', 'race_key', season, round, race_key
    from {{ ref('fct_results') }}
    where race_key = (select key from unknown)

    union all

    select 'fct_results', 'status_key', season, round, status_key
    from {{ ref('fct_results') }}
    where status_key = (select key from unknown)

)

select * from offenders
