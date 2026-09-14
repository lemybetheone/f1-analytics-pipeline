-- Grain: one row per finishing status, plus one Unknown member.
--
-- **Why this dimension exists at all.** SCHEMA §4 marks it optional, and that
-- is fair: PRD §6 theme 6 is already answerable without it. DNF rate comes from
-- `is_classified` on the fact, and "most common retirement reasons" is a group
-- by on the status text. What this adds is a **coarser rollup** — mechanical
-- versus collision versus finished — which the raw 136-value list cannot give,
-- and a surrogate key so the fact stops carrying free text for a categorical.
--
-- **The surrogate hashes `status`, not `status_id`.** Results carry the status
-- *description* and no statusId at all, so the text is the real join key. The
-- text is unique across all 136 rows and every loaded result matches one.
--
-- **The source renamed things over time, so aliasing happens before grouping.**
-- The full-history backfill made this visible: the same concept appears under
-- different names in different eras, and grouping the raw text would split one
-- concept in two at the rename.
--
--     +1 Lap ... +46 Laps   1950-2022  ->  Lapped          2023-2026
--     Withdrew              1950-2023  ->  Did not start   2023-2026
--
-- Left alone, "has the DNS rate changed?" shows a discontinuity at 2023 that is
-- an artefact of vocabulary, not of racing. There are near-duplicates within an
-- era too — Puncture/Tyre puncture, Seat/Driver Seat, Injury/Injured — which
-- split the same cause across two rows and, before this, across two categories.
--
-- **Aliasing is an attribute, never a change to the grain or the key.** The
-- grain stays one row per raw status text and `status_key` still hashes
-- `status`, because that is what `fct_results` hashes when it builds its own
-- `status_key`. Collapsing the rows, or hashing the canonical name, would
-- silently break every fact join. `status_canonical` sits alongside the raw
-- text; the category is derived from the canonical name.
--
-- Renaming also **destroys information**: '+N Laps' says how far down a car
-- finished and 'Lapped' does not. `laps_down` preserves it where the source
-- still carried it — populated 1950-2022, null from 2023. That is a real gap in
-- the source, recorded rather than papered over.
--
-- **The category grouping is curated — it is my judgement, not the source's.**
-- That deserves stating plainly, because it is the one place in this warehouse
-- where an opinion is encoded as data. PRD §6 theme 6 asks for "most common
-- retirement reasons (engine, collision, gearbox, ...)", so these are **causes**
-- rather than outcomes. Whether a driver was classified is a different question
-- and is answered by `fct_results.is_classified`, never from here.
--
-- Two categories name an absence rather than a cause, deliberately:
--
-- * `cause_unspecified` (471 rows) — 'Retired' and 'Not classified'. The source
--   records that the car stopped, or covered too little distance, without
--   saying why. Burying 471 rows in `other` would make the headline answer to
--   "most common retirement reason" a bucket that means nothing. Naming the
--   ignorance is more honest than hiding it, and it is the largest non-engine
--   entry, so it would mislead at the top of the list.
--
-- * `other` (5 rows) — genuine residue. A residue bucket should be small; if it
--   grows, something new arrived upstream and wants a decision.
--
-- **Mechanical remains the fall-through, and the risk is unchanged:** a new
-- non-mechanical status added upstream is silently filed as mechanical.
-- Enumerating all 82 mechanical values instead would mean a new *mechanical*
-- status falling into `other`, which is the same failure pointed the other way,
-- against a list four times longer to maintain. The exception lists below are
-- where a new outcome gets added, and `accepted_values` on `status_category`
-- locks the vocabulary so a typo cannot invent a category.

with statuses as (

    select * from {{ ref('stg_status') }}

),

aliased as (

    select
        status_id,
        status,

        -- The era-normalised name. Derived, never hashed — see the header.
        case
            -- Matched rather than listed: the set grows with race distance, so
            -- '+12 Laps' should not need a code change.
            when status ~ '^\+[0-9]+ Lap' then 'Lapped'

            when status = 'Withdrew'      then 'Did not start'
            when status = 'Excluded'      then 'Disqualified'
            when status = 'Injured'       then 'Injury'
            when status = 'Eye injury'    then 'Injury'
            when status = 'Driver unwell' then 'Illness'
            when status = 'Tyre puncture' then 'Puncture'
            when status = 'Driver Seat'   then 'Seat'
            else status
        end as status_canonical,

        -- How many laps down, where the source still said so. Null from 2023,
        -- when '+N Laps' became a bare 'Lapped' — an information loss upstream,
        -- not a modelling choice.
        case
            when status ~ '^\+[0-9]+ Lap'
            then (regexp_match(status, '^\+([0-9]+) Lap'))[1]::int
        end as laps_down,

        source_all_time_count

    from statuses

),

categorised as (

    select
        -- Hashed from the RAW text, because that is what facts join on.
        -- Hashing `status_canonical` would break every fact join silently.
        {{ dbt_utils.generate_surrogate_key(['status']) }} as status_key,

        status_id,
        status,
        status_canonical,
        laps_down,

        case
            when status_canonical = 'Finished' then 'finished'
            when status_canonical = 'Lapped'   then 'lapped'

            when status_canonical in (
                'Accident', 'Collision', 'Spun off', 'Collision damage',
                'Puncture', 'Damage', 'Fatal accident'
            ) then 'collision'

            -- 'Underweight' is a technical infringement, not a car failure:
            -- the car finished and was struck from the results.
            when status_canonical in ('Disqualified', 'Underweight')
                then 'disqualified'

            when status_canonical in (
                'Did not start', 'Did not qualify', 'Did not prequalify'
            ) then 'did_not_start'

            -- The driver, not the car. Previously split across two categories:
            -- 'Physical' and 'Eye injury' sat in mechanical while 'Injury' and
            -- 'Illness' sat in the catch-all.
            when status_canonical in ('Injury', 'Illness', 'Physical')
                then 'driver_unavailable'

            -- The source records the outcome but not the reason. See header.
            when status_canonical in ('Retired', 'Not classified')
                then 'cause_unspecified'

            when status_canonical in ('Debris', 'Safety', 'Safety concerns',
                                      'Eligibility')
                then 'other'

            -- Everything remaining is a mechanical failure. See the header for
            -- why this is a default rather than an enumeration.
            else 'mechanical'
        end as status_category,

        -- Carried through from staging. Not a metric — it is the API's
        -- all-time occurrence count for the scope we queried.
        source_all_time_count

    from aliased

),

known as (

    select
        status_key,
        status_id,
        status,
        status_canonical,
        laps_down,
        status_category,

        -- **Describes the status, not the driver's classification.** Those are
        -- orthogonal, and conflating them was a real bug caught by comparing
        -- this against the fact's own flag: they disagreed on 20 of 1,244 rows.
        --
        -- Russell, 2024 round 3: status 'Retired' after 56 laps, yet
        -- positionText '17' — the stewards classified him because he covered
        -- enough race distance. The reverse also occurs: status 'Lapped' with
        -- positionText 'R'.
        --
        -- So this answers "did the car reach the end running", while
        -- `stg_results.is_classified` answers "did the stewards classify this
        -- driver" from positionText, the official marker. **Use the fact's flag
        -- for DNF rate**; this one is for describing the status itself.
        status_category in ('finished', 'lapped') as status_implies_running_at_end,

        source_all_time_count,

        false as is_unknown

    from categorised

),

unknown_member as (

    select
        {{ unknown_key() }}        as status_key,

        -- int, not text. Unlike every other dimension here, this natural key
        -- is numeric — stg_status casts it — and a UNION refuses to match
        -- integer against text. Casting explicitly is only half the discipline;
        -- the cast has to match the column's actual type.
        -1::int                    as status_id,
        'Unknown status'::text     as status,
        'Unknown status'::text     as status_canonical,
        null::int                  as laps_down,
        'unknown'::text            as status_category,

        -- Named to match the `known` branch. A UNION takes its column names
        -- from the first branch, so a stale name here was harmless and
        -- misleading — the worst combination to leave in a portfolio repo.
        null::boolean              as status_implies_running_at_end,
        null::int                  as source_all_time_count,
        true                       as is_unknown

)

select * from known
union all
select * from unknown_member
