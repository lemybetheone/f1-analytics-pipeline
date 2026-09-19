-- Visual 7 — Constructor standings, current season (PRD §8b, §6 theme 1)
--
-- The constructors' championship after the most recent completed round. Bar
-- chart: x = team, y = points.
--
-- **The first visual to read `fct_constructor_standings`**, which at 13,635 rows
-- is the largest fact in the warehouse and had been built, tested and unused.
--
-- Simpler than its driver twin in exactly one way: the source gives a single
-- `Constructor` object rather than a list, so there is no array to resolve. It
-- is **not** simpler in the way the model originally claimed — an earlier
-- version of `_facts.yml` said `championship_position` is present on every row,
-- which held for 2024-2026 and was false across 77 seasons (3,107 nulls,
-- including McLaren's 2007 exclusion). Current-season rows are ranked, so the
-- ordering below is safe today; `nulls last` keeps it honest anyway.
--
-- Same snapshot logic as the driver standings: read the latest round, do not
-- sum. `points` is a running total.
--
-- No variables: this card is about *now*.

with current_season as (

    select max(season) as season
    from marts.fct_constructor_standings

),

latest_round as (

    select max(standings.round) as round
    from marts.fct_constructor_standings standings
    join current_season cs on cs.season = standings.season

)

select
    standings.championship_position   as position,
    constructors.constructor_name     as team,
    standings.points                  as points,
    standings.wins                    as wins,
    standings.round                   as after_round

from marts.fct_constructor_standings standings
join current_season cs on cs.season = standings.season
join latest_round   lr on lr.round  = standings.round
join marts.dim_constructor constructors
  on constructors.constructor_key = standings.constructor_key

where not constructors.is_unknown
order by standings.championship_position nulls last, standings.points desc
