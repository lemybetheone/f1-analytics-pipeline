# Security, Data Quality & Governance

> Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Covers secrets handling,
> access control, data quality, observability, and lineage.

---

## 1. Secrets management

### Rules

1. **Real secrets live only in `.env`**, which is git-ignored.
2. **`.env.example` contains placeholders only** — never a real value, not even
   temporarily. It is a tracked file and anything typed into it is destined for
   the remote repository.
3. **Verify before the first commit**, don't assume:
   ```bash
   git check-ignore .env      # must print .env
   git status                 # .env must NOT appear
   ```
4. **Never paste credentials into logs, terminals, chats, or screenshots.**
5. **One config source** — both ingestion and transformation read the same
   environment variables; config files reference env vars, never literals.

### Day-zero setup order

- [x] `.gitignore` created **before** any credential exists on disk
- [x] `.env` created and confirmed ignored (`git check-ignore .env` → `.env`;
      absent from `git status`)
- [x] `.env.example` committed with placeholders and a comment for each value
      explaining where to obtain it
- [x] Connectivity smoke test passes using environment variables only —
      `discovery/probe_connectivity.py`, both systems PASS 2026-08-03

### Rotation policy

Rotate immediately if a credential is written to a tracked file, pasted into a
log or chat, or if its exposure cannot be ruled out.

**Rotation is not complete until the old credential is invalidated.** Order:

1. Create the new credential
2. Update `.env` and **verify the new credential works**
3. **Then** deactivate and delete the old one

> Operational note: managed databases may take several minutes to propagate a
> password reset through their connection pooler. A brief authentication failure
> immediately after a reset is expected behaviour, not a misconfiguration.

## 2. Access control

| Principle | Application |
|---|---|
| **Least privilege** | Storage credentials limited to the single bucket/prefix used |
| **Separate credentials per system** | Object storage and warehouse use distinct credentials |
| **No shared accounts** | Each identity is individually attributable |
| **Short-lived where possible** | Prefer scoped keys; rotate on a schedule |

Scope the storage policy to only the actions the pipeline performs — object
put, get, and list on the project bucket — rather than broad or account-wide
permissions.

## 3. Data classification

| Class | Applies to | Handling |
|---|---|---|
| **Public** | All F1 race / driver / constructor data | No restriction; safe to publish |
| **Secret** | API and cloud credentials, database passwords | Env vars only; never committed; rotate on exposure |

This project processes **no personal data of private individuals** — F1 drivers
and team personnel are public figures in a public dataset. State this explicitly so
reviewers know it was considered rather than overlooked.

### Licensing — confirmed 2026-08-03

**Source:** Jolpica-F1. **Data licence: CC BY-NC-SA 4.0**
([terms](https://github.com/jolpica/jolpica-f1), reviewed against the version
dated 2025-08-27). Three obligations follow, and ShareAlike is the one most
projects miss:

| Clause | Obligation here |
|---|---|
| **BY** (Attribution) | Credit Jolpica-F1 and name the CC BY-NC-SA 4.0 licence with a link, in the README and anywhere data is displayed |
| **NC** (NonCommercial) | Satisfied — a personal portfolio is not "directed toward commercial advantage or monetary compensation". The project must never be monetised, ad-supported, or sold without contacting `admin@jolpi.ca` |
| **SA** (ShareAlike) | **Derivatives of the data inherit the licence.** The star schema, marts and any published extract are adaptations, so they carry CC BY-NC-SA 4.0 — even though the *code* that produces them does not |

**Repository licensing is therefore split**, and stating both is the honest
position:

- **Code** (`ingestion/`, `dbt/`, `discovery/`, `airflow/`) — the author's own
  work, licensed independently. ShareAlike does not reach it: code is not an
  adaptation of the data.
- **Data and data derivatives** (anything in `discovery/findings/`, sample
  payloads, dashboard screenshots, any committed extract) — CC BY-NC-SA 4.0,
  attributed to Jolpica-F1.

**Do not redistribute bulk raw data.** Landing raw JSON in a private object
store is storage, not redistribution. Committing bulk extracts to a public
repository would be redistribution and is out of bounds. Small excerpts kept as
Phase 0 evidence are fine **with attribution** — which the findings files
currently lack and must carry before the repository goes public.

**Terms can change without notice** ("We reserve the right to change these
terms"). The review date above is recorded deliberately; re-check before
publishing.

**No warranty.** The source is volunteer-run and donation-supported, and
explicitly disclaims uptime, availability and correctness. This is why the lake
is the replay source (ARCHITECTURE decision 1): the warehouse must be
rebuildable without re-hitting the API, and a source-freshness failure is an
expected event to handle, not an emergency to page on.

## 4. Data quality framework

Quality is enforced by tests that run on **every build**, not by manual checks.

### Test tiers

| Tier | What | When |
|---|---|---|
| **1 — Grain & keys** | Unique + not-null on the primary/composite key | **Always**, every model |
| **2 — Integrity & domain** | `relationships` on FKs; `not_null` on join keys; `accepted_values` on categoricals | When downstream logic depends on it |
| **3 — Business rules** | Ranges, reconciliation, row-count deltas | Where a violation would mislead analysis |
| **Not tested** | Descriptive/free-text fields nothing joins or filters on | Deliberately omitted |

> **Concrete Tier-3 example (this project):** reconcile a *derived* cumulative
> points total against the *ingested official* standings. A divergence fails the
> build and flags a scoring/modelling bug (missed sprint points, un-applied
> penalty). See the standings decision in [PRD §6](PRD.md#modelling-notes-open-decisions).

### Principles

- **A test must protect a real assumption.** If its failure would not change
  what you do, remove it.
- **Do not test legitimately nullable columns for null.** Confirm nullability
  from observed data and design tests around the truth, not the ideal.
- **Over-testing is a failure mode**: it slows builds and causes alert fatigue,
  which trains people to ignore genuine failures.
- **Declare and enforce grain on every model** — the single highest-value test.

### Source freshness

Configure freshness thresholds on sources so stale upstream data fails loudly
rather than silently producing outdated dashboards.

## 5. Reliability & observability

| Concern | Mechanism |
|---|---|
| Transient upstream errors | Retry with exponential backoff |
| Record-level failures | Dead-letter table; monitored and retried |
| Run failures | Orchestrator alerting (email/webhook) |
| Silent data loss | Row-count checks; dead-letter must trend to zero |
| Stale data | Source freshness tests |
| Regressions | CI runs tests on every PR |

**Monitor the dead-letter table.** A growing backlog means the pipeline is
losing data even though runs report success.

## 6. Lineage & documentation

- **Generated, not hand-written** — `dbt docs generate` produces the lineage
  graph and column-level documentation; publish it so reviewers can browse it.
- **Descriptions are part of the contract** — every model states its grain and
  purpose. If a description asserts a rule (for example, that a column is one of
  a fixed set of values), enforce it with a test. *A description is a promise; a
  test is enforcement.*
- **Decision log** — non-obvious choices are recorded in
  [ARCHITECTURE §7](ARCHITECTURE.md#7-decision-log-adr-lite).

## 7. Change management

- All changes via **pull request**; CI must be green to merge.
  > **Status (2026-08-03):** the pull-request half is in effect from Phase 1.
  > The CI half is **not yet enforceable** — no workflow exists until Phase 4
  > (PRD §9). Recorded rather than left implied, so the document does not assert
  > a gate that isn't running. Phase 0's five commits predate this and went
  > directly to `main`.
- **Conventional commits** — the history should read as a narrative of the
  project.
- **Schema changes are versioned in code.** The DDL or migration is committed
  and applied through a repeatable process, never applied ad hoc in a console
  and left undocumented.

> Note on idempotent DDL: `CREATE TABLE IF NOT EXISTS` silently skips tables
> that already exist, so re-running a schema file does **not** apply changes to
> an existing table. Either adopt a migration tool or make migrations explicit,
> ordered, and committed.

## 8. Retention & cost

| Concern | Policy |
|---|---|
| Lake objects | Retain raw JSON (immutable, enables replay); revisit as free-tier limits approach |
| Warehouse | Monitor against free-tier storage limits |
| High-cardinality volume (laps / pit stops / telemetry) | Cap deliberately; set the bound in Phase 0 and document in the PRD |
| Cost | $0 — free tiers only; verify no billable resources are provisioned |

## 9. Pre-flight checklist

Complete **before** writing pipeline code. Verified 2026-08-03 unless noted.

- [x] `.gitignore` in place; `.env` verified ignored
- [x] `.env.example` has placeholders only
- [x] Credentials scoped to least privilege — **both sides, verified
      mechanically by `discovery/probe_connectivity.py`.** The S3 IAM user has
      `ListBucket`/`GetObject`/`PutObject` on one bucket and no `DeleteObject`,
      so a leaked key cannot destroy the replay source; the probe also asserts
      it is not the account root. The warehouse connects as `f1_pipeline`
      (migration 002), which owns `raw`/`staging`/`marts` and holds no
      `SUPERUSER`, `CREATEDB`, `CREATEROLE` or `BYPASSRLS`. Blast radius of a
      leaked warehouse credential is three schemas rebuildable from the lake
- [x] Connectivity smoke test passes for storage **and** warehouse
- [x] Rate limits and quotas documented — 4/s burst, 500/hr sustained
- [x] Data classification reviewed — §3
- [x] Source licensing and attribution reviewed — CC BY-NC-SA 4.0, §3
- [x] Test tiers agreed — §4
- [ ] Alerting destination decided — **outstanding**, due in Phase 3 with the
      orchestrator

**Environment as verified:** Supabase PostgreSQL 17.6 via the session pooler
(`ap-southeast-1`), schemas `raw` / `staging` / `marts` created. Lake:
S3 bucket in `ap-southeast-1`, public access blocked, reached by a scoped IAM
user with no `DeleteObject`.

> Concrete resource identifiers — bucket name, project ref, account id — live in
> `.env` and are deliberately **not** recorded here. They are not credentials,
> but publishing them in a public repository invites probing and lets a deleted
> bucket name be claimed by someone else. The connectivity probe reads them from
> the environment and prints them locally, which is where they belong.

#### History sweep, 2026-09-19 — one accepted exception

Run before making the repository public. All 82 commits searched, by path and by
content, for every live secret value and for key-shaped strings.

**No credential has ever been committed.** `PROFILE.md`, `CLAUDE.md`,
`*.private.md` and `.env` appear in no commit. `WAREHOUSE_PASSWORD`,
`REPORTING_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and
`WAREHOUSE_USER` appear in no commit. No `AKIA…`, `ghp_…`, `sk-…`, `xox…` or
PEM private key appears in any commit. `.env.example` has held blank
placeholders in every version, and `dbt/profiles.yml` has only ever used
`env_var()`.

**The exception:** the **bucket name** appears in 8 historical blobs of this
file. The rule above was introduced by `e219fea — docs: keep concrete resource
identifiers out of the repository`, which removed it from the working tree;
commits before that still carry it, and git history keeps every version.

**Accepted rather than purged**, deliberately:

- It is an identifier, not a credential. Nothing in history grants access to the
  bucket, which blocks public access and is reached only by a scoped IAM user
  with no `DeleteObject`.
- Purging means rewriting all 82 commits, changing every SHA — or deleting and
  recreating the repository, which destroys **25 pull requests** whose
  descriptions record the reasoning behind most of the decisions in this project.
  That history is a more valuable asset than the name of a private bucket is a
  liability.

**What would change this.** The stated risk is squatting: if the bucket is ever
deleted, the name is claimable by anyone who has read this history. So the
bucket must not simply be deleted — it is renamed or replaced first, or retained.
Renaming it at any point makes the historical mention inert, which is the cheap
fix whenever it is convenient.

The rule itself stands unchanged for everything written from here on.
