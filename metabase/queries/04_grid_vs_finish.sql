-- Visual 4 — Grid vs finish (PRD §8b, §6 theme 3)
--
-- Drivers who gain the most places between the grid and the flag. Bar chart:
-- x = driver, y = avg_positions_gained.
--
-- WHAT `positions_gained` ALREADY EXCLUDES
-- ---------------------------------------
-- The measure is built in `fct_results` and is deliberately **null** for two
-- cases rather than zero (`_facts.yml`):
--
--   * **Retirements** — a driver who crashed on lap 5 did not "lose" fifteen
--     places, and counting it as a loss would punish unreliability twice.
--   * **Pit lane starts** — `grid_position` 0 is a real value meaning no grid
--     slot, so there is nothing to gain *from*. Treating 0 as pole would show
--     an enormous fictional gain.
--
-- `avg()` skips nulls, so this query inherits both exclusions for free. That is
-- the modelling layer doing its job: the measure was made honest once, and
-- every chart built on it is honest by default rather than by remembering.
--
-- THE SMALL-SAMPLE TRAP
-- --------------------
-- An average over three races is noise. Without a floor this chart is a list of
-- drivers who had one good afternoon in 1954 — which is why `{{min_races}}`
-- exists and is **required**, not optional. A chart whose correctness depends
-- on someone remembering to set a filter is a chart that will eventually be
-- read wrong.
--
-- The count is of races that *count toward the average* — classified finishes
-- from a grid slot — not of career starts. A driver with 200 starts and 5
-- classified finishes should not qualify on the strength of the 200.
--
-- `not d.is_unknown` excludes the Unknown member. No fact should ever land on
-- it (`assert_no_facts_on_unknown_members` asserts exactly that), so this is
-- belt and braces — but a reporting query that silently averages a sentinel row
-- into a real answer is the kind of thing nobody notices.
--
-- THE CAVEAT THAT MATTERS MOST
-- ---------------------------
-- **This measures starting position at least as much as driver skill**, and a
-- reader who takes it for "the best overtakers" will be badly wrong. Measured
-- across the full history:
--
--     from grid  1:  900 races, avg gain -1.34
--     from grid  2:  889 races, avg gain -1.20
--     from grid 10:  731 races, avg gain +2.00
--     from grid 20:  583 races, avg gain +7.50
--
-- A pole sitter cannot gain anything and can only lose; a driver starting 20th
-- gains seven places by finishing where the car belongs. So the leaderboard
-- selects for drivers who habitually started near the back in cars that could
-- move forward — which is why it reads as a list of long careers in mid-field
-- machinery rather than a list of champions.
--
-- `avg_grid` is therefore returned alongside, so the bias is visible in the
-- chart rather than known only to whoever wrote the query. A caveat in a
-- description is read once; a column is read every time.
--
-- Metabase variables: {{min_races}} (Number, required, default 100) and
-- {{top_n}} (Number, required, default 15).

select
    d.full_name                          as driver,
    count(*)                             as races_counted,
    round(avg(f.positions_gained), 2)    as avg_positions_gained,
    round(avg(f.grid_position), 1)       as avg_grid,
    max(f.positions_gained)              as best_single_race

from marts.fct_results f
join marts.dim_driver d using (driver_key)

where f.positions_gained is not null
  and not d.is_unknown

group by driver
having count(*) >= {{min_races}}
order by avg_positions_gained desc
limit {{top_n}}
