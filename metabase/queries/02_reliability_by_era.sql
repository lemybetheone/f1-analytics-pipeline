-- Visual 2 — Reliability by era (PRD §8b, §6 theme 6)
--
-- DNF rate per decade across 77 seasons. Bar chart: x = decade, y = dnf_rate (formatted as Percent in Metabase).
--
-- **The headline visual.** 50% of the field failing to finish in the 1950s
-- against 13% today is the clearest evidence the historical backfill bought
-- something three modern seasons could not — and the 1980s bump is the turbo
-- era, which is a real fact about the sport rather than an artefact.
--
-- THE DENOMINATOR IS A DECISION, SO IT IS STATED
-- ----------------------------------------------
-- This is **DNF rate per start, not per entry**: a car that never started did
-- not fail to finish. Rows whose status is `withdrawn_or_dns` are therefore
-- excluded from both halves of the fraction.
--
-- It matters more than it looks. Measured across the full history the two
-- definitions differ by 0.1 to 3.4 points, the gap being widest in the 1960s
-- (48.3% per entry against 44.9% per start), where non-starters were common.
-- A chart labelled "DNF rate" with no stated denominator is a chart two people
-- can read differently.
--
-- WHY NO `dim_race` JOIN
-- ----------------------
-- PRD §8b originally specified `fct_results × dim_race`, on the assumption that
-- the season had to come from the race dimension. It does not: `season` is
-- denormalised onto the fact (ARCHITECTURE decision 27, "denormalise what is
-- filtered on, not what is joined for"). This query is that decision paying
-- off — the join it avoids is the one every era-based slice would otherwise
-- need. `dim_status` is joined because excluding non-starters requires it.
--
-- `is_classified` rather than `dim_status.status_implies_running_at_end`:
-- the two disagree on real rows and answer different questions. This one reads
-- `positionText`, the stewards' own marker (see `_facts.yml`).
--
-- No Metabase variables — the whole point is the full sweep.

select
    (f.season / 10) * 10                                as decade,
    count(*)                                            as starts,
    count(*) filter (where not f.is_classified)         as dnfs,
    -- A **fraction**, not a pre-scaled percentage. Metabase's Percent format
    -- multiplies by 100, so returning 49.4 here renders as "4940%". Letting
    -- the presentation layer do the presenting is the right split anyway:
    -- this column is a ratio, and how it is displayed is a chart decision.
    round(
        count(*) filter (where not f.is_classified)::numeric / count(*),
        4
    )                                                   as dnf_rate

from marts.fct_results f
join marts.dim_status st using (status_key)

-- Per start, not per entry. See the header.
where st.status_category <> 'withdrawn_or_dns'

group by decade
order by decade
