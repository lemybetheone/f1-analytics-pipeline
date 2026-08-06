-- 002 — dedicated pipeline role (least privilege, SECURITY §2)
--
-- WHY
-- ---
-- The pipeline currently connects as Supabase's `postgres` role, which is
-- effectively superuser. A leaked or mishandled credential would therefore
-- have rights over the entire database, not just this project. SECURITY §2
-- requires least privilege; this is the warehouse half of it. The lake half is
-- already done — an IAM user scoped to one bucket with no DeleteObject.
--
-- WHAT THIS ROLE CAN DO
-- ---------------------
-- Own and fully manage `raw`, `staging` and `marts`. Nothing else. It cannot
-- create databases or roles, cannot replicate, and cannot bypass row-level
-- security. If this credential leaks, the blast radius is three schemas that
-- can be rebuilt from the lake.
--
-- PASSWORD
-- --------
-- Deliberately absent from this file. It is tracked, so anything typed here is
-- destined for the remote. Set it out of band in step 2 of the runbook below.
--
-- RUN AS
-- ------
-- `postgres`, in the Supabase SQL editor. Paste this file verbatim.

-- ---------------------------------------------------------------------------
-- 1. The role
-- ---------------------------------------------------------------------------
-- The NO* attributes are already the defaults. They are stated explicitly
-- because a least-privilege role should be readable as such without the reader
-- having to know what PostgreSQL defaults to.
create role f1_pipeline with
    login
    nosuperuser
    nocreatedb
    nocreaterole
    noreplication
    nobypassrls;

comment on role f1_pipeline is
    'Ingestion and dbt. Owns raw/staging/marts only. Created by migration 002.';

-- Resolve unqualified names against the project schemas, then public. Without
-- this, dbt and ad hoc queries need every object fully qualified.
alter role f1_pipeline set search_path = raw, staging, marts, public;

-- A runaway query on a free tier is a real failure mode. Ten minutes is far
-- longer than any legitimate statement in this pipeline.
alter role f1_pipeline set statement_timeout = '10min';

-- ---------------------------------------------------------------------------
-- 1b. Let the current role hand ownership over
-- ---------------------------------------------------------------------------
-- PostgreSQL 16 changed this: reassigning an object's owner now requires the
-- current role to be able to SET ROLE to the new owner. Creating the role does
-- not grant that, so without this line the next block fails with
-- "must be able to SET ROLE f1_pipeline".
--
-- This grants membership, not privilege escalation: `postgres` already
-- outranks `f1_pipeline` in every respect. It also has to stay in place —
-- future migrations that reassign ownership need it again.
grant f1_pipeline to current_user with set true;

-- ---------------------------------------------------------------------------
-- 2. Ownership of the project schemas
-- ---------------------------------------------------------------------------
-- Ownership rather than a list of grants: dbt creates and drops models
-- constantly, and an owner needs no re-granting each time a new object appears.
-- Granting piecemeal would mean a permission error every time a model is added.
alter schema raw     owner to f1_pipeline;
alter schema staging owner to f1_pipeline;
alter schema marts   owner to f1_pipeline;

grant usage, create on schema raw     to f1_pipeline;
grant usage, create on schema staging to f1_pipeline;
grant usage, create on schema marts   to f1_pipeline;

-- ---------------------------------------------------------------------------
-- 3. Existing objects
-- ---------------------------------------------------------------------------
-- None exist yet. Included so the migration is correct rather than merely
-- correct-today: re-running it against a populated database still does the
-- right thing.
grant all privileges on all tables    in schema raw, staging, marts to f1_pipeline;
grant all privileges on all sequences in schema raw, staging, marts to f1_pipeline;
grant all privileges on all functions in schema raw, staging, marts to f1_pipeline;

-- ---------------------------------------------------------------------------
-- 4. Future objects created by postgres
-- ---------------------------------------------------------------------------
-- Objects f1_pipeline creates itself are owned by it and need nothing. This
-- covers the other direction: anything `postgres` creates in these schemas
-- later (a migration run as postgres, a manual fix) stays reachable by the
-- pipeline instead of silently becoming invisible to it.
alter default privileges for role postgres in schema raw, staging, marts
    grant all privileges on tables to f1_pipeline;
alter default privileges for role postgres in schema raw, staging, marts
    grant all privileges on sequences to f1_pipeline;

-- ---------------------------------------------------------------------------
-- 5. Verify — run this and read the output before moving on
-- ---------------------------------------------------------------------------
-- Expect: one row, all four "can_*" columns false, and three schemas owned by
-- f1_pipeline. Anything else means stop and investigate rather than proceed.
select
    rolname                       as role,
    rolcanlogin                   as can_login,
    rolsuper                      as can_super,
    rolcreatedb                   as can_create_db,
    rolcreaterole                 as can_create_role,
    rolbypassrls                  as can_bypass_rls
from pg_roles
where rolname = 'f1_pipeline';

select
    nspname                       as schema,
    pg_get_userbyid(nspowner)     as owner
from pg_namespace
where nspname in ('raw', 'staging', 'marts')
order by nspname;

-- ---------------------------------------------------------------------------
-- ROLLBACK (do not run unless reverting)
-- ---------------------------------------------------------------------------
-- Ownership must return to postgres *before* the role can be dropped —
-- PostgreSQL refuses to drop a role that still owns objects.
--
--     alter schema raw     owner to postgres;
--     alter schema staging owner to postgres;
--     alter schema marts   owner to postgres;
--     alter default privileges for role postgres in schema raw, staging, marts
--         revoke all privileges on tables from f1_pipeline;
--     alter default privileges for role postgres in schema raw, staging, marts
--         revoke all privileges on sequences from f1_pipeline;
--     drop owned by f1_pipeline;
--     drop role f1_pipeline;
--
-- Then restore WAREHOUSE_USER / WAREHOUSE_PASSWORD in .env to the postgres
-- values and re-run the connectivity probe.
