# Metabase

The serving layer. PRD §8b specifies the tool choice and the visuals; this is
how to run it.

## Start it

```bash
cd metabase
docker compose up -d
```

Then <http://localhost:3000>. First run asks you to create a local admin
account and takes a minute or two to boot — the healthcheck allows for that.

## Connect it to the warehouse

Add a **PostgreSQL** database with the `REPORTING_*` values from `.env`:

| Field | Value |
|---|---|
| Host / Port / Database | same as `WAREHOUSE_HOST` / `WAREHOUSE_PORT` / `WAREHOUSE_DATABASE` |
| Username | `REPORTING_USER` — the full `f1_reporting.<project-ref>` |
| Password | `REPORTING_PASSWORD` |
| Schemas | `marts` only |
| SSL | on, mode `require` |

**Never `f1_pipeline`.** That role owns raw, staging and marts, so a dashboard
holding it could drop the tables it draws from. `f1_reporting` (migration 007)
has `select` on `marts` and nothing else — verified 11/11 against what it cannot
do, not just what it can.

The username must carry the `.<project-ref>` suffix: Supabase's session pooler
routes by `<role>.<ref>`, and a bare `f1_reporting` fails with *"tenant or user
not found"*, which reads exactly like a wrong password.

## `queries/` is the source of truth

Metabase keeps its saved questions in its **own** application database, which is
not in git and cannot be reviewed in a pull request. So each visual's SQL lives
here as well, and the two can drift.

**A change belongs in `queries/` first**, then gets pasted into Metabase. Each
file's header carries the reasoning — the denominator decisions, the measures
that are deliberately null, the traps — which no Metabase description has room
for.

| File | Visual | §6 theme |
|---|---|---|
| `01_championship_progression.sql` | Championship progression | 1 |
| `02_reliability_by_era.sql` | Reliability by era | 6 |
| `03_why_cars_retire.sql` | Why cars retire | 6 |
| `04_grid_vs_finish.sql` | Grid vs finish | 3 |

Files use Metabase's `{{variable}}` and optional `[[ ... ]]` syntax, so they are
not runnable as plain SQL without substitution.

## Your dashboards live in a Docker volume

Metabase stores every saved question and dashboard in `metabase_data`, an
embedded H2 database — **not** in the warehouse and not in this repository.

`docker compose down` is safe. **`docker compose down -v` deletes every
dashboard you have built**, with no confirmation. That is the real "will this
still work next year" risk here, and it has nothing to do with licensing.

To back it up:

```bash
docker run --rm -v metabase_data:/from -v "$PWD":/to alpine \
    tar czf /to/metabase-backup.tgz -C /from .
```

## Why local rather than hosted

PRD §8b has the full reasoning. Briefly: Metabase is a stateful JVM application
needing an always-on process and ~2 GB, so free hosting does not really exist
for it — Vercel and Netlify cannot run it at all, and Metabase Cloud is
~$85/month. The deliverable is therefore **screenshots in the README**, which
every reader sees and which cannot rot, plus a live demo in person when it
matters.
