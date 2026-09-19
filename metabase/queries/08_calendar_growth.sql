-- Visual 8 — Calendar growth (PRD §8b, All time tab)
--
-- Races per season, 1950 to 2026. Line or bar: x = season, y = races.
--
-- **The visual that makes "77 seasons" legible at a glance.** Eight races in
-- 1950 against twenty-three today, and the shape of the climb — flat through
-- the fifties, steady growth from the seventies, a visible dip in 2020 — is a
-- history of the sport in one line. Reliability by era shows the same span but
-- reads as a trend; this reads as coverage.
--
-- **Combo chart: `scheduled` as bars, `run` as a line.** The mark types are
-- doing real work. Measured, the two series differ in exactly **1 of 77
-- seasons** — the current one — so drawn as two lines they overlap invisibly
-- for the whole span and read as a single line with an unexplained kink at the
-- right edge. A line over bars tracks them exactly until 2026 and then visibly
-- drops away, which says "this season is partway through" without a caption.
--
-- What `run` is **not** is an integrity check, and an earlier version of this
-- comment claimed it was — "a gap before the current year would mean
-- `has_been_run` had drifted". That was hollow: `dim_race.has_been_run` *is*
-- `race_date <= current_date`, so a past race failing it is arithmetically
-- impossible. The series earns its place by being readable, not by testing
-- anything. Correctness assertions belong in dbt, where they can fail a build.
--
-- Reads `dim_race` alone — no fact needed. The calendar is a property of the
-- event dimension, and a season with a scheduled-but-unraced round has no fact
-- rows at all, so counting facts here would undercount by design.
--
-- No variables.

select
    season                                      as season,
    count(*)                                    as scheduled,
    count(*) filter (where has_been_run)        as run

from marts.dim_race

where not is_unknown
group by season
order by season
