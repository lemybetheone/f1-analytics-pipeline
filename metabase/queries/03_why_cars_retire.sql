-- Visual 3 — Why cars retire (PRD §8b, §6 theme 6)
--
-- Retirement causes, grouped. Bar chart: x = cause, y = retirements
-- (or `share`, formatted as Percent).
--
-- This is what the `dim_status` rework was for. The source ships 136 distinct
-- status strings — "Engine", "+3 Laps", "Withdrew", "Fatal accident" — which is
-- a list, not an answer. `status_category` rolls them into causes someone can
-- actually read, and that grouping is curated judgement rather than the
-- source's: it is the one place in this warehouse where an opinion is encoded
-- as data (ARCHITECTURE decisions 33, 34).
--
-- SCOPE
-- -----
-- **Retirements only.** Non-finishers, with non-starters excluded — a car that
-- never started did not retire. Same denominator decision as visual 2, and for
-- the same reason.
--
-- `lapped` appearing here is not a bug. A handful of rows carry a status saying
-- the car was still circulating while `positionText` says the stewards did not
-- classify it; the two answer different questions and disagree on real rows.
-- `is_classified` is the authority on whether a driver finished (`_facts.yml`).
--
-- `cause_unspecified` is honest rather than lazy: those are statuses where the
-- source records that the car stopped without saying why ("Retired", "Not
-- classified"). Folding them into `other` would have made the headline answer a
-- bucket that means nothing.
--
-- Metabase variable: {{decade}} (Number, **optional**, no default). The
-- double-bracket clause is only applied when the variable has a value, so the
-- chart defaults to all 77 seasons and filters to one decade when asked.
-- Compare 1980 against 2010 — mechanical failure dominates the turbo era and
-- recedes sharply as reliability improves.

select
    st.status_category                                  as cause,
    count(*)                                            as retirements,

    -- A fraction, for Metabase's Percent format. See visual 2's header: the
    -- Percent type multiplies by 100, so a pre-scaled number renders 100x too
    -- large.
    round(count(*)::numeric / sum(count(*)) over (), 4)  as share

from marts.fct_results f
join marts.dim_status st using (status_key)

where not f.is_classified
  and st.status_category <> 'withdrawn_or_dns'
  [[ and (f.season / 10) * 10 = {{decade}} ]]

group by cause
order by retirements desc
