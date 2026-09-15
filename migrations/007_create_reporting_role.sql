-- 007 — read-only reporting role for the dashboard (least privilege, SECURITY §2)
--
-- WHY
-- ---
-- Phase 4 points a BI tool at this warehouse. The only credential that exists
-- today is `f1_pipeline`, which **owns** raw, staging and marts — so a
-- dashboard connecting with it could drop every table it draws from. That is a
-- large blast radius for something whose entire job is `select`.
--
-- Migration 002 made the same argument about connecting as `postgres`. This is
-- the next rung down: the pipeline needs write access because it writes; a
-- dashboard does not.
--
-- WHAT THIS ROLE CAN DO
-- ---------------------
-- `select` on `marts`. Nothing else. It cannot see `raw` or `staging`, cannot
-- create objects anywhere, and cannot log in to anything but this database.
-- Reporting reads the modelled layer — if a question cannot be answered from
-- `marts`, the answer is a new model, not a wider grant.
--
-- THE PART THAT IS EASY TO GET WRONG
-- ----------------------------------
-- `grant select on all tables in schema marts` applies to the tables that exist
-- **right now**. dbt drops and recreates every mart on each `dbt build`, and a
-- recreated table is a *new* object that inherits none of yesterday's grants.
-- Without section 4 below, this role would work today and silently lose access
-- the next time the DAG runs — a dashboard that breaks overnight for no visible
-- reason.
--
-- `alter default privileges for role f1_pipeline` is what makes the grant
-- durable, because `f1_pipeline` is the role dbt connects as and therefore the
-- creator of every future mart.
--
-- PASSWORD
-- --------
-- Deliberately absent from this file. It is tracked, so anything typed here is
-- destined for the remote. Set it out of band, then store it in `.env` only.
--
-- RUN AS
-- ------
-- `postgres`, in the Supabase SQL editor — creating a role needs CREATEROLE,
-- which `f1_pipeline` deliberately does not have. Then adopt it into the
-- ledger with `python migrations/apply.py --mark-applied 007`.

-- ---------------------------------------------------------------------------
-- 1. The role
-- ---------------------------------------------------------------------------
-- The NO* attributes are already the defaults. Stated explicitly so a reader
-- can see this is least-privilege without knowing PostgreSQL's defaults.
create role f1_reporting with
    login
    nosuperuser
    nocreatedb
    nocreaterole
    noreplication
    nobypassrls;

comment on role f1_reporting is
    'Read-only dashboard access to marts. Created by migration 007.';

-- Only `marts` — an unqualified `dim_driver` resolves, an unqualified
-- `results` does not, which is the intended shape of this role's world.
alter role f1_reporting set search_path = marts, public;

-- Shorter than the pipeline's 10 minutes. A dashboard query that runs longer
-- than two minutes is a modelling problem, not a query to wait out, and this
-- is a free tier shared with the pipeline.
alter role f1_reporting set statement_timeout = '2min';

-- ---------------------------------------------------------------------------
-- 2. Membership, so section 4 can run
-- ---------------------------------------------------------------------------
-- `alter default privileges for role f1_pipeline` requires the current role to
-- be a member of `f1_pipeline`. Migration 002 granted this and noted it must
-- stay; re-granting is harmless and makes this file runnable on its own.
grant f1_pipeline to current_user with set true;

-- ---------------------------------------------------------------------------
-- 3. Access to the modelled layer, and nothing else
-- ---------------------------------------------------------------------------
-- `usage` on the schema is the door; `select` on the tables is the furniture.
-- Both are needed, and neither is granted on raw or staging.
grant usage on schema marts to f1_reporting;
grant select on all tables in schema marts to f1_reporting;

-- Stated rather than assumed: this role must never create objects in marts.
-- `create` is not granted by default, so this is belt and braces against a
-- future migration granting it broadly.
revoke create on schema marts from f1_reporting;

-- ---------------------------------------------------------------------------
-- 4. Future objects — the durable half
-- ---------------------------------------------------------------------------
-- Every `dbt build` recreates the marts as `f1_pipeline`. Without this, the
-- grants in section 3 cover only today's tables and the dashboard loses access
-- on the next DAG run. See the header.
alter default privileges for role f1_pipeline in schema marts
    grant select on tables to f1_reporting;

-- ---------------------------------------------------------------------------
-- 5. Verify — run this and read the output before moving on
-- ---------------------------------------------------------------------------
-- Expect: one row, every "can_*" column false.
select
    rolname        as role,
    rolcanlogin    as can_login,
    rolsuper       as can_super,
    rolcreatedb    as can_create_db,
    rolcreaterole  as can_create_role,
    rolbypassrls   as can_bypass_rls
from pg_roles
where rolname = 'f1_reporting';

-- Expect: marts = true, raw and staging = false.
select
    nspname                                            as schema,
    has_schema_privilege('f1_reporting', nspname, 'usage')  as can_use,
    has_schema_privilege('f1_reporting', nspname, 'create') as can_create
from pg_namespace
where nspname in ('raw', 'staging', 'marts')
order by nspname;

-- Expect: one row per mart, select true and insert/update/delete all false.
select
    c.relname                                                   as table_name,
    has_table_privilege('f1_reporting', c.oid, 'select')        as can_select,
    has_table_privilege('f1_reporting', c.oid, 'insert')        as can_insert,
    has_table_privilege('f1_reporting', c.oid, 'update')        as can_update,
    has_table_privilege('f1_reporting', c.oid, 'delete')        as can_delete
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'marts' and c.relkind in ('r', 'v')
order by c.relname;

-- Expect: one row showing a default grant of SELECT to f1_reporting in marts.
-- If this is empty, section 4 did not take and the dashboard will break on the
-- next `dbt build` rather than today.
select
    pg_get_userbyid(d.defaclrole) as granted_by,
    n.nspname                     as schema,
    d.defaclacl                   as default_privileges
from pg_default_acl d
join pg_namespace n on n.oid = d.defaclnamespace
where n.nspname = 'marts';

-- ---------------------------------------------------------------------------
-- ROLLBACK (do not run unless reverting)
-- ---------------------------------------------------------------------------
--     alter default privileges for role f1_pipeline in schema marts
--         revoke select on tables from f1_reporting;
--     revoke all on all tables in schema marts from f1_reporting;
--     revoke all on schema marts from f1_reporting;
--     drop owned by f1_reporting;
--     drop role f1_reporting;
--
-- Then remove REPORTING_USER / REPORTING_PASSWORD from `.env`.
