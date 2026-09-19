-- Visual 6 — Driver standings, current season (PRD §8b, §6 theme 1)
--
-- Where the championship stands after the most recent completed round. Table.
--
-- **Reads the snapshot at its latest round rather than summing anything.**
-- `fct_driver_standings` is a periodic snapshot: the row for the latest round
-- already *is* the season-to-date total, penalties and sprint points included,
-- as the source publishes it. Deriving it from `fct_results` instead would
-- reimplement scoring rules the source already applied — which is exactly what
-- `assert_standings_reconcile_with_results` exists to check, not to replace.
--
-- **The constructor comes from the array, not from a join to one team.** A
-- driver who changes team mid-season has two, which is why
-- `fct_driver_standings.constructor_ids` is an array and not a foreign key
-- (`_facts.yml`). Flattening it to one would silently pick a winner; 62 rows in
-- the full history carry two.
--
-- `championship_position` is legitimately null where the source declines to
-- rank an entirely tied field — everyone on zero points early in a season. The
-- ordering falls back to points so the table stays sensible when that happens.
--
-- No variables: this card is about *now*. Use Championship progression for a
-- season selector.

with current_season as (

    select max(season) as season
    from marts.fct_driver_standings

),

latest_round as (

    select max(standings.round) as round
    from marts.fct_driver_standings standings
    join current_season cs on cs.season = standings.season

)

select
    standings.championship_position          as position,
    drivers.full_name                        as driver,
    teams.constructor_names                  as team,
    standings.points                         as points,
    standings.wins                           as wins,
    standings.round                          as after_round,
    standings.is_unranked                    as is_unranked

from marts.fct_driver_standings standings
join current_season cs  on cs.season = standings.season
join latest_round   lr  on lr.round  = standings.round
join marts.dim_driver drivers on drivers.driver_key = standings.driver_key

-- Resolve the array to readable names without collapsing it to one team.
left join lateral (
    select string_agg(constructors.constructor_name, ' / '
                      order by constructors.constructor_name)
               as constructor_names
    from unnest(standings.constructor_ids) as season_constructor_id
    join marts.dim_constructor constructors
      on constructors.constructor_id = season_constructor_id
) teams on true

where not drivers.is_unknown
order by standings.championship_position nulls last, standings.points desc
