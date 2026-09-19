-- Visual 10 — Teammate head-to-head (PRD §8b, §6 theme 4)
--
-- Who beat their teammate, and how often. Bar chart: one row per pairing,
-- `driver_a_ahead` and `driver_b_ahead` as two series.
--
-- **This is the visual the star schema was shaped for.** PRD §6 theme 4 is the
-- reason `driver_key` and `constructor_key` both sit on `fct_results` rather
-- than the constructor living on `dim_driver`: a driver's team changes within a
-- season, so it cannot be a driver attribute, but at race grain it is
-- single-valued. That makes "same race, same team, different driver" a self-join
-- on two keys — no bridge table, no fan-out.
--
-- THE METHODOLOGICAL DECISION
-- --------------------------
-- **The record counts only races where both drivers were classified.** A
-- teammate who retires on lap 3 is not evidence that the other was faster, and
-- counting it would make the head-to-head a reliability table wearing a pace
-- table's name. `races_compared` is therefore usually well short of the season's
-- round count, and that gap is information rather than a defect.
--
-- Points are counted across **all** races, not just compared ones, because
-- points are the actual outcome and reliability is part of it. The two columns
-- deliberately answer different questions, and a pairing where one driver leads
-- the head-to-head while the other leads on points is the interesting case.
--
-- `classified_position` is the right column here and the wrong one for "did
-- they finish": it is non-null even for retirements (`_facts.yml`). Restricting
-- to `is_classified` on both sides is what makes comparing it meaningful.
--
-- WHY `a.driver_key < b.driver_key`
-- ---------------------------------
-- A self-join yields each pairing twice, once in each direction. The inequality
-- keeps one. It is on the surrogate key rather than the name because the key is
-- what the join uses and what the grain is built on; ordering by name would be
-- a display choice masquerading as a join condition.
--
-- A constructor that ran more than two drivers in a season produces more than
-- one pairing, which is correct — they were teammates, just not for the whole
-- year. `races_compared` shows how much of the season each pairing covers.
--
-- Metabase variables: {{season}} (Number, required, default 2026) and
-- {{min_races}} (Number, required, default 3) — a pairing decided by one shared
-- finish is not a record.

with teammate_races as (

    select
        a.constructor_key                                   as constructor_key,
        a.driver_key                                        as driver_a_key,
        b.driver_key                                        as driver_b_key,
        a.classified_position                               as position_a,
        b.classified_position                               as position_b,
        a.points                                            as points_a,
        b.points                                            as points_b,
        (a.is_classified and b.is_classified)               as both_classified

    from marts.fct_results a
    join marts.fct_results b
      on  b.race_key        = a.race_key
      and b.constructor_key = a.constructor_key
      and b.driver_key      > a.driver_key   -- one row per pairing, not two

    where a.season = {{season}}

),

tallied as (

    select
        constructor_key,
        driver_a_key,
        driver_b_key,

        count(*) filter (where both_classified)                     as races_compared,
        count(*) filter (where both_classified
                           and position_a < position_b)             as driver_a_ahead,
        count(*) filter (where both_classified
                           and position_b < position_a)             as driver_b_ahead,

        sum(points_a)                                               as driver_a_points,
        sum(points_b)                                               as driver_b_points

    from teammate_races
    group by constructor_key, driver_a_key, driver_b_key

),

named as (

    -- **Order the pair by who won, not by surrogate key.** `driver_a` above is
    -- simply whoever has the lower `driver_key` — an MD5 hash — so which driver
    -- lands in which column is effectively random and a reader cannot predict
    -- it. That is fine for a self-join condition and useless for a chart.
    --
    -- Reordering here means the **first name is always the one who leads the
    -- head-to-head**, which makes a stacked bar readable without a legend
    -- lookup. Ties break on name so the output is deterministic.
    select
        constructors.constructor_name as team,
        tallied.races_compared        as races_compared,

        case when tallied.driver_a_ahead > tallied.driver_b_ahead
               or (tallied.driver_a_ahead = tallied.driver_b_ahead
                   and driver_a.full_name < driver_b.full_name)
             then driver_a.full_name else driver_b.full_name end  as leader,
        greatest(tallied.driver_a_ahead, tallied.driver_b_ahead)  as leader_wins,

        case when tallied.driver_a_ahead > tallied.driver_b_ahead
               or (tallied.driver_a_ahead = tallied.driver_b_ahead
                   and driver_a.full_name < driver_b.full_name)
             then driver_b.full_name else driver_a.full_name end  as trailer,
        least(tallied.driver_a_ahead, tallied.driver_b_ahead)     as trailer_wins,

        case when tallied.driver_a_ahead > tallied.driver_b_ahead
               or (tallied.driver_a_ahead = tallied.driver_b_ahead
                   and driver_a.full_name < driver_b.full_name)
             then tallied.driver_a_points else tallied.driver_b_points end
                                                                  as leader_points,
        case when tallied.driver_a_ahead > tallied.driver_b_ahead
               or (tallied.driver_a_ahead = tallied.driver_b_ahead
                   and driver_a.full_name < driver_b.full_name)
             then tallied.driver_b_points else tallied.driver_a_points end
                                                                  as trailer_points

    from tallied
    join marts.dim_constructor constructors
      on constructors.constructor_key = tallied.constructor_key
    join marts.dim_driver driver_a on driver_a.driver_key = tallied.driver_a_key
    join marts.dim_driver driver_b on driver_b.driver_key = tallied.driver_b_key

)

select
    -- The label carries the answer, so the chart needs no legend lookup.
    -- `team` alone would not do: it repeats when a constructor ran more than
    -- two drivers in a season — RB and Williams both do, in 2024 and 2026 — and
    -- a bar keyed on it would silently merge two real pairings.
    named.team || ': ' ||
        split_part(named.leader,  ' ', -1) || ' ' ||
        named.leader_wins || '-' || named.trailer_wins || ' ' ||
        split_part(named.trailer, ' ', -1)               as pairing,

    named.team                                          as team,
    named.races_compared                                as races_compared,

    named.leader                                        as leader,
    named.leader_wins                                   as leader_wins,
    named.trailer                                       as trailer,
    named.trailer_wins                                  as trailer_wins,

    named.leader_points                                 as leader_points,
    named.trailer_points                                as trailer_points

from named
where named.races_compared >= {{min_races}}
order by named.races_compared desc, named.team
