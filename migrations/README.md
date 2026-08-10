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

```bash
python migrations/apply.py --status     # what is applied, what is pending
python migrations/apply.py              # apply everything pending
```

The runner connects with the **pipeline credentials**, which matters: the
Supabase SQL editor connects as `postgres`, so anything created there would be
owned by `postgres` rather than `f1_pipeline` — the ownership split migration
002 exists to prevent. Each migration commits in one transaction with its
ledger row, so a failure can never leave a record claiming success.

Applied versions are tracked in `raw.schema_migrations`. That lives in `raw`
rather than a dedicated `meta` schema because the pipeline role owns only
`raw`/`staging`/`marts` and cannot create new schemas — least privilege working
as designed.

`--mark-applied NNN` records a version **without running it**, for adopting a
database whose early migrations were applied by hand.

| # | File | Applied | Notes |
|---|---|---|---|
| 001 | `001_create_schemas.sql` | 2026-08-03 | Applied by `discovery/probe_connectivity.py --create-schemas` before this directory existed; adopted into the ledger with `--mark-applied` |
| 002 | `002_create_pipeline_role.sql` | 2026-08-04 | Password set out of band. Verified: connects as `f1_pipeline`, owns all three schemas, holds no `SUPERUSER` / `CREATEDB` / `CREATEROLE` / `BYPASSRLS`. Adopted with `--mark-applied` — re-running it would fail on an existing role |
| 003 | `003_raw_ingestion_tables.sql` | 2026-08-06 | First migration applied by the runner. `raw.results`, `raw.failed_ingestions`, `raw.ingestion_checkpoints`, all owned by `f1_pipeline` |
| 004 | `004_raw_reference_and_session_tables.sql` | 2026-08-07 | `raw.seasons`, `circuits`, `drivers`, `constructors`, `status`, `races`, `qualifying`, `sprint` |
| 006 | `006_api_call_log.sql` | 2026-08-09 | `raw.api_call_log`. Makes the 500/hour budget survive process boundaries and restarts — it was previously per-process, so a three-season ingest spent ~120 calls while no process saw more than 72 |
| 005 | `005_raw_race_scoped_tables.sql` | 2026-08-08 | `raw.pitstops`, `raw.driver_standings`, `raw.constructor_standings`. Pit stops key on `(season, round, driver, stop)` — without `stop`, 368 of 825 rows would collide |
