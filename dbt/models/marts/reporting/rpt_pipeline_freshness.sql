-- Grain: one row. The dashboard's "data as of" card.
--
-- **The first model in the reporting layer, and it exists for a specific
-- reason.** ARCHITECTURE §1 states that no aggregate layer is built and that
-- the trigger for starting one is a need the star schema cannot serve — not
-- volume. This is that need: the dashboard must answer "how current is this?",
-- and the honest answer lives in `raw`, which the read-only reporting role
-- deliberately cannot see (migration 007). Widening that grant to answer one
-- question would trade a security boundary for convenience. Surfacing the
-- answer into `marts` through a tested model does not.
--
-- WHY NOT JUST `max(ingested_at)`
-- ------------------------------
-- Because it answers a different question. The warehouse upsert only writes
-- when the payload actually differs, so a run that succeeds and correctly finds
-- nothing new leaves every `ingested_at` untouched. Measured on 2026-09-17: the
-- pipeline ran at 03:01 UTC while the newest `ingested_at` still read
-- 2026-09-14, because no race had happened in between.
--
-- A card built on that would have announced "3 days stale" about a pipeline
-- that was working perfectly. **"When did we last check" and "when did
-- something last change" are different facts, and a freshness indicator needs
-- the first.** Both are returned below, named for what they mean.
--
-- THE DURABLE SIGNAL IS THE CHECKPOINT TABLE
-- ------------------------------------------
-- `api_call_log` is pruned to a trailing window on every run, so it answers
-- "when did the pipeline last spend budget" and goes empty if a run makes no
-- calls. `ingestion_checkpoints` is never pruned, so `max(updated_at)` survives
-- and is the one to trust. Both are here because they disagree in a useful way:
-- calls without checkpoint movement means pages were fetched but nothing
-- landed.
--
-- `failed_scopes` is not decoration. A scope whose checkpoint reads `failed`
-- means an extract died mid-run and was never resumed, which is invisible in
-- row counts — the data simply stops being complete without anything erroring.

{{ config(materialized='table') }}

with ingestion_activity as (

    select
        max(updated_at)                                  as last_ingestion_at,
        count(*) filter (where status = 'failed')        as failed_scopes,
        count(*) filter (where status = 'in_progress')   as unfinished_scopes
    from {{ source('raw', 'ingestion_checkpoints') }}

),

api_activity as (

    select
        max(called_at)                                              as last_api_call_at,
        count(*) filter (where called_at > now() - interval '1 hour') as api_calls_last_hour
    from {{ source('raw', 'api_call_log') }}

),

-- When a fact row last actually changed. Deliberately separate from the two
-- above: this moves only when the source amends something.
data_changed as (

    select max(greatest(ingested_at, updated_at)) as last_data_change_at
    from {{ ref('fct_results') }}

),

current_season as (

    select max(season) as season
    from {{ ref('dim_race') }}
    where not is_unknown

),

calendar as (

    select
        r.season                                                as season,
        count(*) filter (where r.has_been_run)                  as rounds_run,
        count(*)                                                as rounds_scheduled,
        max(r.race_date) filter (where r.has_been_run)          as last_race_date,
        min(r.race_date) filter (where not r.has_been_run)      as next_race_date
    from {{ ref('dim_race') }} r
    join current_season cs on cs.season = r.season
    where not r.is_unknown
    group by r.season

)

select
    calendar.season                       as season,
    calendar.rounds_run                   as rounds_run,
    calendar.rounds_scheduled             as rounds_scheduled,
    calendar.last_race_date               as last_race_date,
    calendar.next_race_date               as next_race_date,

    ingestion_activity.last_ingestion_at  as last_ingestion_at,
    api_activity.last_api_call_at         as last_api_call_at,
    data_changed.last_data_change_at      as last_data_change_at,

    ingestion_activity.failed_scopes      as failed_scopes,
    ingestion_activity.unfinished_scopes  as unfinished_scopes,
    api_activity.api_calls_last_hour      as api_calls_last_hour,

    -- Stamped at build time so the card can distinguish "the pipeline has not
    -- run" from "this model has not been rebuilt". Without it, a stale dbt
    -- build and a stale ingest look identical on the dashboard.
    '{{ run_started_at }}'::timestamptz   as model_built_at

from calendar
cross join ingestion_activity
cross join api_activity
cross join data_changed
