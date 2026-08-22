-- Grain: one row per finishing status (136 rows).
--
-- Two things worth knowing before using this model.
--
--   1. **Results join to this table by `status` text, not by `status_id`.**
--      The results payload carries the description and no `statusId` at all.
--      The text is unique across all 136 rows and every loaded result matches
--      one, so uniqueness is tested on it — load-bearing here in a way that
--      `circuit_name` and `constructor_name` are not.
--
--   2. **`source_all_time_count` is not an attribute of the status.** It is an
--      aggregate the API computes for whatever scope was requested: the 136
--      values sum to 26,115, the all-time results total, while `raw.results`
--      currently holds far fewer. Renamed from the source's bare `count` to
--      say so. Kept as a reconciliation input — after the historical backfill,
--      counting the results fact by status should reproduce these figures
--      exactly — and must not be used as a metric.

with source as (

    select * from {{ source('raw', 'status') }}

),

renamed as (

    select
        status_id::int                    as status_id,
        payload ->> 'status'              as status,
        (payload ->> 'count')::int        as source_all_time_count,

        source_key,
        ingested_at,
        updated_at
    from source

)

select * from renamed
