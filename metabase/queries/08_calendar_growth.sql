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
-- Two series, deliberately. `scheduled` counts every round on the calendar and
-- `run` counts those that have happened, so the current season shows as an
-- incomplete bar rather than a misleading short one. They are identical for
-- every completed season, which is itself worth seeing: a gap anywhere before
-- the current year would mean `has_been_run` had drifted.
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
