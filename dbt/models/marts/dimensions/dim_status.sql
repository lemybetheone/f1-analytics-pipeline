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
-- **The category grouping is curated — it is my judgement, not the source's.**
-- That deserves stating plainly, because it is the one place in this warehouse
-- where an opinion is encoded as data. The rule enumerates every non-mechanical
-- outcome and lets everything else fall through to `mechanical`. Checked
-- against all 136: the 82 that fall through are Engine, Gearbox, Suspension,
-- Transmission, Electrical, Brakes, Clutch, Turbo, Hydraulics, Overheating and
-- similar — mechanical without exception.
--
-- **The risk that creates:** a *new* non-mechanical status added upstream would
-- be silently filed as mechanical. The alternative — defaulting to
-- 'unclassified' — puts 6,654 all-time rows into a bucket that means nothing
-- and answers no question, which is worse. The exception lists below are where
-- a new outcome gets added. A seed file was considered and rejected for the
-- same reason: 136 hand-maintained rows would leave a new status with no row
-- at all, which is a null join rather than a wrong label.

with statuses as (

    select * from {{ ref('stg_status') }}

),

categorised as (

    select
        -- Hashed from the text, because that is what facts join on.
        {{ dbt_utils.generate_surrogate_key(['status']) }} as status_key,

        status_id,
        status,

        case
            when status = 'Finished' then 'finished'

            -- 31 statuses follow the '+N Lap(s)' pattern, plus 'Lapped'
            -- itself. Matched rather than listed: the set grows with race
            -- distance, and '+12 Laps' should not need a code change.
            when status ~ '^\+[0-9]+ Lap' then 'lapped'
            when status = 'Lapped' then 'lapped'

            when status in (
                'Accident', 'Collision', 'Spun off', 'Collision damage',
                'Puncture', 'Damage'
            ) then 'collision'

            when status = 'Disqualified' then 'disqualified'

            when status in (
                'Withdrew', 'Did not start', 'Did not qualify',
                'Did not prequalify', 'Not classified', 'Retired',
                'Injured', 'Injury', 'Illness', 'Driver Seat',
                'Safety concerns', 'Eligibility', 'Excluded',
                'Debris', 'Safety', 'Fatal accident', 'Driver unwell'
            ) then 'non_start_or_other'

            -- Everything remaining is a mechanical failure. See the header for
            -- why this is a default rather than an enumeration.
            else 'mechanical'
        end as status_category,

        -- Carried through from staging. Not a metric — it is the API's
        -- all-time occurrence count for the scope we queried, and it will
        -- disagree with anything counted from the facts until the historical
        -- backfill completes. Kept as the input to that reconciliation.
        source_all_time_count

    from statuses

),

known as (

    select
        status_key,
        status_id,
        status,
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
        'unknown'::text            as status_category,
        null::boolean              as is_classified_finish,
        null::int                  as source_all_time_count,
        true                       as is_unknown

)

select * from known
union all
select * from unknown_member
