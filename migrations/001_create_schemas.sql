-- 001 — project schemas
--
-- One schema per layer, so the layer contract in ARCHITECTURE §2 is enforced by
-- object ownership rather than by convention alone: nothing outside dbt writes
-- to staging or marts.
--
-- Already applied 2026-08-03 by discovery/probe_connectivity.py --create-schemas,
-- before this directory existed. Recorded here so a fresh environment can be
-- rebuilt from the repository alone. Re-running it is a no-op.

create schema if not exists raw;
create schema if not exists staging;
create schema if not exists marts;

comment on schema raw is
    'Source payloads as landed. No business logic, no filtering.';
comment on schema staging is
    'One view per raw table: rename, cast, convert to UTC. No joins or aggregation.';
comment on schema marts is
    'Dimensional model: dimensions, facts, aggregates. Read by BI only.';
