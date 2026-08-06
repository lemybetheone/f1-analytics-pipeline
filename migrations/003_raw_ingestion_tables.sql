-- 003 — raw layer for results, plus the dead-letter and checkpoint tables
--
-- Grain, keys and load pattern all come from Phase 0 measurements, not from
-- assumption. See SCHEMA §2 and discovery/findings/.

-- ---------------------------------------------------------------------------
-- raw.results — grain: one row per (season, round, driver)
-- ---------------------------------------------------------------------------
-- Load pattern: UPSERT, not insert-do-nothing.
--
-- A finished race looks like a textbook immutable event, but F1 results are
-- *adjudicated*: a stewards' penalty, disqualification or appeal amends a
-- published result days later. Insert-do-nothing would freeze the pre-penalty
-- record while the record books disagree.
--
-- The three key columns were measured at 100% presence across all 26,115
-- result rows from 1950 to 2024, so none of them can be null. Everything else
-- stays in the payload untouched: raw stores what the source returned, and
-- casting belongs to staging (SCHEMA §3). That matters here because the source
-- returns *every* scalar as a string, including numbers.
create table if not exists raw.results (
    season      text        not null,
    round       text        not null,
    driver_id   text        not null,
    payload     jsonb       not null,

    -- Lineage: which lake object this row was loaded from. The warehouse loads
    -- from the lake (ARCHITECTURE decision 2), so every row can be traced back
    -- to the immutable file it came from and the load replayed.
    source_key  text        not null,

    -- Split deliberately. ingested_at is when we first saw the row; updated_at
    -- moves when an upsert changes it. A row where updated_at > ingested_at is
    -- a result the sport amended after publication - which makes the
    -- adjudication finding observable rather than merely documented.
    ingested_at timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    constraint raw_results_pkey primary key (season, round, driver_id)
);

comment on table raw.results is
    'Grain: one row per (season, round, driver). Upserted - F1 results are adjudicated and amended after publication.';

-- ---------------------------------------------------------------------------
-- raw.failed_ingestions — dead letter
-- ---------------------------------------------------------------------------
-- ARCHITECTURE §5: one bad record must never kill a run, and must never vanish
-- silently. SECURITY §5 adds that this table must trend to zero - a growing
-- backlog means the pipeline is losing data while reporting success.
create table if not exists raw.failed_ingestions (
    id             bigint generated always as identity primary key,
    entity         text        not null,

    -- Enough context to replay the exact failing request without guessing.
    request_url    text        not null,
    request_params jsonb,

    attempt_count  integer     not null,
    error_class    text        not null,
    error_detail   text,

    -- Set for record-level failures; null when the whole request failed.
    record_payload jsonb,

    failed_at      timestamptz not null default now(),

    -- Null until retried successfully. Without this there is no way to tell a
    -- current backlog from a historical log, and "trends to zero" is
    -- unmeasurable.
    resolved_at    timestamptz
);

create index if not exists failed_ingestions_unresolved_idx
    on raw.failed_ingestions (entity, failed_at)
    where resolved_at is null;

comment on table raw.failed_ingestions is
    'Dead letter. Must trend to zero; unresolved rows mean silent data loss.';

-- ---------------------------------------------------------------------------
-- raw.ingestion_checkpoints — resumable backfill
-- ---------------------------------------------------------------------------
-- The full backfill is ~3,925 calls against a 500/hour limit, so roughly eight
-- hours (ARCHITECTURE decision 16). A job that long will be interrupted, so
-- resuming without re-fetching is a requirement rather than a nicety.
--
-- State lives in the warehouse (decision 23) so the checkpoint and the data it
-- describes commit in the same transaction and cannot disagree after a crash.
create table if not exists raw.ingestion_checkpoints (
    entity         text        not null,

    -- The unit of work being tracked, e.g. 'season=2024'. Kept as free text
    -- because scoping differs per endpoint: results page by season, pit stops
    -- require season and round.
    scope          text        not null,

    -- Where to resume. The API caps pages at 100 rows and silently clamps
    -- larger requests, so offsets advance in fixed steps.
    next_offset    integer     not null default 0,
    total_expected integer,

    status         text        not null default 'in_progress',
    started_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),

    constraint ingestion_checkpoints_pkey primary key (entity, scope),
    constraint ingestion_checkpoints_status_ck
        check (status in ('in_progress', 'complete', 'failed'))
);

comment on table raw.ingestion_checkpoints is
    'Resume state for interrupted backfills. Committed with the data it describes.';
