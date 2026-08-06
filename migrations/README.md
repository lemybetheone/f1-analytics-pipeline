# Migrations

Ordered, committed schema changes. SECURITY §7: *"Schema changes are versioned
in code. The DDL or migration is committed and applied through a repeatable
process, never applied ad hoc in a console and left undocumented."*

## Rules

- **Numbered and immutable.** Once a migration is applied, it is never edited —
  a change means a new file. Editing an applied migration means two databases
  claiming the same version with different contents.
- **Idempotent where it can be, honest where it cannot.** `create ... if not
  exists` is safe to re-run, but it **silently skips objects that already
  exist**, so it does *not* apply changes to an existing table. Any alteration
  gets its own migration rather than an edit to the original.
- **No credentials.** These files are tracked. Passwords are set out of band and
  stored only in `.env`.

## Applying

Until a migration runner is introduced, apply in numeric order via the Supabase
SQL editor, pasting the committed file **verbatim**. The requirement is that
what ran is what is committed — not that a particular tool ran it.

| # | File | Applied | Notes |
|---|---|---|---|
| 001 | `001_create_schemas.sql` | 2026-08-03 | Applied by `discovery/probe_connectivity.py --create-schemas` before this directory existed. Recorded here so a fresh environment can be rebuilt from the repository alone |
| 002 | `002_create_pipeline_role.sql` | 2026-08-04 | Password set out of band. Verified: connects as `f1_pipeline`, owns all three schemas, holds no `SUPERUSER` / `CREATEDB` / `CREATEROLE` / `BYPASSRLS` |
