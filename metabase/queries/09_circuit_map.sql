-- Visual 9 — Circuit map (PRD §8b, §6 theme 5, All time tab)
--
-- Every circuit F1 has raced at, plotted. Metabase renders latitude/longitude
-- natively — set the visualisation to Map, pin type, and size or colour by
-- `races_held`.
--
-- **78 circuits across 34 countries.** This is the only visual where the scale
-- of the backfill is felt rather than read: three modern seasons would plot
-- roughly two dozen pins clustered in Europe and the Gulf, and the sport's
-- whole geography — Watkins Glen, Kyalami, Zandvoort's first life, Adelaide —
-- would simply be absent.
--
-- `first_season` and `last_season` make the map a history rather than a
-- snapshot: a circuit last used in 1976 sits beside one used last month, and
-- the difference is visible in a tooltip.
--
-- **Counts races held, not results.** A circuit's significance is how often it
-- hosted, and joining through the fact would weight by grid size — which
-- changed from 20-odd cars to 40 and back, and would make the fifties look more
-- important than they were.
--
-- Both `is_unknown` filters matter here: the Unknown circuit carries null
-- coordinates, which a map silently drops, and the Unknown race would inflate a
-- count with no real event behind it.
--
-- No variables.

select
    circuits.circuit_name          as circuit,
    circuits.locality              as locality,
    circuits.country               as country,
    circuits.latitude              as latitude,
    circuits.longitude             as longitude,

    count(*)                       as races_held,
    min(races.season)              as first_season,
    max(races.season)              as last_season

from marts.dim_circuit circuits
join marts.dim_race races on races.circuit_key = circuits.circuit_key

where not circuits.is_unknown
  and not races.is_unknown
  and races.has_been_run

group by circuits.circuit_name, circuits.locality, circuits.country,
         circuits.latitude, circuits.longitude
order by races_held desc
