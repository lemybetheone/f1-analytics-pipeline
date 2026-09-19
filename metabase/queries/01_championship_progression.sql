-- Visual 1 — Championship progression (PRD §8b, §6 theme 1)
--
-- Points by round for a season's leading contenders. Line chart: x = round,
-- y = points, one series per driver.
--
-- These files are the versioned copy of what is pasted into Metabase. Metabase
-- keeps its questions in its own application database, which is not in git and
-- not reviewable — so the SQL lives here as well. The two can drift; this is
-- the source of truth, and a change belongs here first.
--
-- **Plotting the running total is correct here, and is the one place it is.**
-- `fct_driver_standings` is a periodic snapshot: each row restates a
-- season-to-date total rather than what was scored in that round. Charting it
-- directly is exactly right — *summing* it across rounds is what would be
-- wrong, counting every point once per subsequent round (see `_facts.yml`).
--
-- Contenders are picked by their **final** standing rather than their points at
-- each round, so the legend is stable: a driver leading at round 3 and
-- finishing eleventh does not appear and disappear.
--
-- Metabase variables: {{season}} (Number, default 2026) and {{top_n}} (Number,
-- default 5). The default is the **current** season because this card sits on
-- the `Current season` tab; the selector is what makes it historical on demand.
--
-- Verified as `f1_reporting` before being pasted in — 2024 returns Verstappen on
-- 437, which is the official total the standings reconciliation test checks
-- against, so switching the selector to 2024 is a live correctness check.

with final_round as (

    select max(round) as round
    from marts.fct_driver_standings
    where season = {{season}}

),

contenders as (

    select st.driver_key, st.championship_position
    from marts.fct_driver_standings st
    join final_round f on f.round = st.round
    where st.season = {{season}}
      and st.championship_position <= {{top_n}}

)

select
    st.round                 as round,
    r.race_name              as race,
    d.full_name              as driver,
    st.points                as points,
    c.championship_position  as final_position

from marts.fct_driver_standings st
join contenders       c on c.driver_key = st.driver_key
join marts.dim_driver d on d.driver_key = st.driver_key
join marts.dim_race   r on r.season = st.season
                       and r.round  = st.round

where st.season = {{season}}
order by c.championship_position, st.round
