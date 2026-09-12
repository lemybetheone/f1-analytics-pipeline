-- Grain: one row per (season, round), plus one Unknown member.
--
-- The event dimension. Facts join here for season, round and date context, and
-- through here to `dim_circuit` for circuit-level rollups (PRD §6 theme 5).
--
-- Three things make this dimension different from the three before it.
--
--   1. **Composite natural key.** The surrogate hashes `(season, round)`
--      together. `generate_surrogate_key` casts each part to text and joins
--      them with a separator, so (2024, 11) and (20, 2411) cannot collide.
--
--   2. **It joins.** The first model in the project to do so — staging forbids
--      joins (SCHEMA §3), and the two dimensions before this had nothing to
--      join to. It is an inner join on purpose: every one of the 1,172 races
--      resolves to a circuit, and `dim_circuit` additionally carries an Unknown
--      member, so a race arriving without a match would mean the reference load
--      had drifted. Failing the `not_null` test on `circuit_key` is the right
--      outcome there, rather than silently routing to Unknown and hiding it.
--
--   3. **It carries both keys to the circuit.** `circuit_key` is what facts
--      join through, per SCHEMA §5. `circuit_id` is kept beside it for
--      traceability back to `raw.races` — the same reason every model here
--      keeps its natural key. The cost is honest denormalisation: two columns
--      for one relationship, which stay in step only because both are derived
--      in this model from the same source column. The purist alternative is
--      `circuit_key` alone, forcing every lookup through the dimension; that
--      would be right if the natural key were large or sensitive, and here it
--      is a short slug whose traceability is worth more.
--
-- The schedule includes races that have not happened: 10 of 1,172 at the time
-- of writing. They are kept. A dimension describes events, and a scheduled race
-- is a real event with a real date — facts simply have no rows for it yet.
-- `has_been_run` makes the distinction explicit so consumers filter on meaning
-- rather than re-deriving a date comparison.

with races as (

    select * from {{ ref('stg_races') }}

),

circuits as (

    select circuit_id, circuit_key from {{ ref('dim_circuit') }}

),

known as (

    select
        {{ dbt_utils.generate_surrogate_key(['races.season', 'races.round']) }}
                                        as race_key,

        races.season,
        races.round,
        races.race_name,

        -- Both keys to the circuit, for the reasons in note 3.
        circuits.circuit_key,
        races.circuit_id,

        races.race_date,
        races.race_start_utc,

        races.practice_1_utc,
        races.practice_2_utc,
        races.practice_3_utc,
        races.qualifying_utc,
        races.sprint_utc,
        races.sprint_qualifying_utc,

        races.is_sprint_weekend,

        -- Derived rather than left to each consumer: the schedule carries
        -- future races, and "has this happened" is asked far more often than
        -- the date comparison behind it.
        races.race_date <= current_date as has_been_run,

        races.wikipedia_url,

        false as is_unknown

    from races
    inner join circuits on circuits.circuit_id = races.circuit_id

),

unknown_member as (

    select
        {{ unknown_key() }}             as race_key,
        -1::int                         as season,
        -1::int                         as round,
        'Unknown race'::text            as race_name,
        {{ unknown_key() }}             as circuit_key,
        '-1'::text                      as circuit_id,
        null::date                      as race_date,
        null::timestamptz               as race_start_utc,
        null::timestamptz               as practice_1_utc,
        null::timestamptz               as practice_2_utc,
        null::timestamptz               as practice_3_utc,
        null::timestamptz               as qualifying_utc,
        null::timestamptz               as sprint_utc,
        null::timestamptz               as sprint_qualifying_utc,
        null::boolean                   as is_sprint_weekend,
        null::boolean                   as has_been_run,
        null::text                      as wikipedia_url,
        true                            as is_unknown

)

select * from known
union all
select * from unknown_member
