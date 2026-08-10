-- 006 — shared record of API calls, so the rate budget survives the process
--
-- WHY
-- ---
-- The source publishes 500 requests/hour and returns **no rate-limit headers**,
-- so remaining allowance cannot be read at runtime — it has to be counted by
-- the client. Until now that count lived in memory, which means it was correct
-- within a single process and blind across them.
--
-- Two situations make that a real problem rather than a theoretical one:
--
--   * `tasks.py ingest` spawns a subprocess per entity group. Loading three
--     seasons spent ~120 calls while no single process ever saw more than 72.
--   * The historical backfill runs for hours and **will** be interrupted. A
--     resumed run starts with an empty counter and would happily spend a second
--     full allowance inside the same hour.
--
-- The terms permit blocking without notice, so exceeding the budget risks
-- losing the source entirely — the one dependency that cannot be rebuilt from
-- the lake.
--
-- WHY THE WAREHOUSE
-- -----------------
-- Same reasoning as the checkpoint table (ARCHITECTURE decision 23): a local
-- file does not survive a machine change and is invisible to the orchestrator,
-- and any shared store beats a per-process guess. One row per call at 500/hour
-- is trivial volume, and rows older than the window are pruned on startup.

create table if not exists raw.api_call_log (
    id        bigint      generated always as identity primary key,
    called_at timestamptz not null default now(),

    -- Which endpoint spent the call. Not needed for the budget itself, but it
    -- turns the log into an answer to "what consumed the allowance" when a run
    -- gets throttled — otherwise the table records that budget was spent while
    -- being unable to say on what.
    entity    text
);

-- The only query this table serves: how many calls fall inside the trailing
-- window, and when the oldest of them was.
create index if not exists api_call_log_called_at_idx
    on raw.api_call_log (called_at desc);

comment on table raw.api_call_log is
    'One row per source API call. Backs the 500/hour budget across processes and restarts.';
