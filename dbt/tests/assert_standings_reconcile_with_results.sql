-- The reconciliation PRD §6 commits to: a points total derived from the race
-- and sprint facts must equal the official standings the source publishes.
--
-- This is the Tier-3 business-rule test SECURITY §4 describes. It is the
-- highest-value test in the marts layer, because it is the only one that can
-- catch a *modelling* error rather than a structural one — a missed sprint, a
-- points column cast wrong, a join that fans out. Every grain and
-- relationships test in the project would pass happily while the numbers were
-- wrong.
--
-- It already earned its keep: it is what revealed that `fct_results` alone
-- does not reconcile. 2024 official 437 for Verstappen against 399 derived —
-- the 38 missing points were sprint, and the finding is what produced
-- `fct_sprint_results` (ARCHITECTURE decision 28).
--
-- **Compared only at the final round of a completed season.** Mid-season rounds
-- would require the derived total to be filtered to races up to that round,
-- which re-implements the running total rather than checking it, and the
-- current season has rounds still to run.
--
-- Half points are why the comparison is exact rather than rounded: `points` is
-- numeric throughout precisely so 12.5 survives, and a tolerance here would
-- hide the kind of error this test exists to find.

with completed_seasons as (

    -- A season is comparable only once every scheduled round has been run.
    select season
    from {{ ref('dim_race') }}
    where not is_unknown
    group by season
    having bool_and(has_been_run)

),

final_round as (

    select season, max(round) as final_round
    from {{ ref('dim_race') }}
    where season in (select season from completed_seasons)
    group by season

),

official as (

    select s.season, s.driver_key, s.points as official_points
    from {{ ref('fct_driver_standings') }} s
    join final_round f
      on f.season = s.season
     and f.final_round = s.round

),

derived as (

    select season, driver_key, sum(points) as derived_points
    from (
        select season, driver_key, points from {{ ref('fct_results') }}
        union all
        select season, driver_key, points from {{ ref('fct_sprint_results') }}
    ) all_sessions
    where season in (select season from completed_seasons)
    group by season, driver_key

)

select
    official.season,
    official.driver_key,
    official.official_points,
    coalesce(derived.derived_points, 0) as derived_points,
    official.official_points - coalesce(derived.derived_points, 0) as difference
from official
left join derived
       on derived.season = official.season
      and derived.driver_key = official.driver_key
where official.official_points is distinct from coalesce(derived.derived_points, 0)
