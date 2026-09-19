-- Visual 5 — Data freshness (PRD §8b, Current season tab)
--
-- The "data as of" card. Reads `rpt_pipeline_freshness`, the one model in the
-- reporting layer, which exists because the honest answer lives in `raw` and
-- the reporting role deliberately cannot see `raw` (migration 007).
--
-- **Three timestamps that mean three different things**, and conflating them is
-- the whole reason this card is built the way it is:
--
--   * `last_ingestion_at`   — when the pipeline last did work. The one to trust.
--   * `last_api_call_at`    — when it last spent API budget.
--   * `last_data_change_at` — when a row last actually changed.
--
-- The third is *not* a freshness signal. The upsert only writes when a payload
-- differs, so a run that succeeds and correctly finds nothing new leaves it
-- untouched. Measured 2026-09-17: ingestion ran at 03:04 while the newest data
-- change still read 2026-09-14, three days earlier, because no race had
-- happened in between. A card built on that alone would have called a healthy
-- pipeline stale.
--
-- `hours_since_ingestion` is derived here rather than stored, because it is
-- only true at the moment it is read.
--
-- Display as a table or a set of number cards. No variables.

select
    freshness.season                                        as season,
    freshness.rounds_run || ' of ' || freshness.rounds_scheduled
                                                            as season_progress,
    freshness.last_race_date                                as last_race,
    freshness.next_race_date                                as next_race,

    freshness.last_ingestion_at                             as pipeline_last_ran,
    round(
        extract(epoch from (now() - freshness.last_ingestion_at)) / 3600.0,
        1
    )                                                       as hours_since_run,

    freshness.last_data_change_at                           as data_last_changed,

    -- Both should be 0. A nonzero `failed_scopes` means an extract died and was
    -- never resumed — invisible in row counts, because the data just quietly
    -- stops being complete.
    freshness.failed_scopes                                 as failed_scopes,
    freshness.unfinished_scopes                             as unfinished_scopes,
    freshness.api_calls_last_hour                           as api_calls_last_hour,

    freshness.model_built_at                                as models_last_built

from marts.rpt_pipeline_freshness freshness
