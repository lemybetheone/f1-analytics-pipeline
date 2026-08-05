-- 002 — dedicated pipeline role (least privilege, SECURITY §2)
--
-- WHY: the pipeline currently connects as Supabase's `postgres` role, which is
-- effectively superuser. A leaked or mishandled credential would therefore have
-- rights over the entire database, not just this project. SECURITY §2 requires
-- least privilege; this is the warehouse half of it. The lake half is already
-- done (an IAM user scoped to one bucket with no DeleteObject).
--
-- PASSWORD: deliberately absent from this file. It is tracked, so anything
-- typed here is destined for the remote. After running this migration, set the
-- password out of band and put it in .env only:
--
--     alter role f1_pipeline with password '<generated-password>';
--
-- THEN update .env and re-run the connectivity probe BEFORE deleting anything:
--
--     WAREHOUSE_USER=f1_pipeline.<project-ref>     <- note the pooler suffix
--     WAREHOUSE_PASSWORD=<generated-password>
--
--     python discovery/probe_connectivity.py --warehouse-only
--
-- The pooler expects `<role>.<project-ref>`. A bare `f1_pipeline` fails with a
-- tenant error that reads exactly like a wrong password.
--
-- Run as `postgres` in the Supabase SQL editor.

-- LOGIN, and nothing more. No SUPERUSER, no CREATEDB, no CREATEROLE, no BYPASSRLS.
create role f1_pipeline with login noinherit;

-- Own the three project schemas outright. Ownership rather than a grant list
-- means dbt can create and drop its own models without needing rights to be
-- re-granted every time a new object appears.
alter schema raw     owner to f1_pipeline;
alter schema staging owner to f1_pipeline;
alter schema marts   owner to f1_pipeline;

grant usage, create on schema raw, staging, marts to f1_pipeline;

-- Existing objects in those schemas (none yet, but this makes the migration
-- correct rather than merely correct-today).
grant all privileges on all tables    in schema raw, staging, marts to f1_pipeline;
grant all privileges on all sequences in schema raw, staging, marts to f1_pipeline;

comment on role f1_pipeline is
    'Ingestion and dbt. Owns raw/staging/marts only. Created by migration 002.';
