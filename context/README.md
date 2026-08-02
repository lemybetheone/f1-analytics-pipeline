# Context — plan before you build

Planning documents for the F1 Analytics Pipeline. These are written before
execution and act as the **source of truth** for the project: scope, design,
schema, and governance. Work should follow them, and they should be updated
when reality disagrees.

## Read in this order

| # | Document | Answers |
|---|---|---|
| 1 | [PRD.md](PRD.md) | What are we building, for whom, and when is it done? |
| 2 | [ARCHITECTURE.md](ARCHITECTURE.md) | How is it built, and why those choices? |
| 3 | [SCHEMA.md](SCHEMA.md) | What are the tables, grains, and the dimensional model? |
| 4 | [SECURITY_AND_GOVERNANCE.md](SECURITY_AND_GOVERNANCE.md) | How are secrets, quality, and lineage handled? |

## How to use these

- **Timebox the planning.** ~4–6 hours total. These documents exist to direct
  the build, not to replace it.
- **They are living documents.** Update them when reality disagrees — a stale
  plan is worse than no plan.
- **Scope control:** before starting any new work, run it through
  [PRD § Change control](PRD.md#change-control). If it fails, it goes to the
  Parking Lot.
- **Log decisions as you go** in
  [ARCHITECTURE § Decision log](ARCHITECTURE.md#7-decision-log-adr-lite).
  Recording *why* a choice was made is as valuable as the choice itself.

## The three gates

Do not proceed past a gate until it is satisfied.

### Gate 1 — Discovery before design
Validate every API payload and prove connectivity to storage and the warehouse
**before** designing any table. Schemas written from assumptions rather than
observed payloads are the most common source of rework: container types, date
representations, nullability, and row caps are routinely different from what
documentation implies.

See [SCHEMA § Phase 0](SCHEMA.md#phase-0-gate--complete-before-designing-any-table).

### Gate 2 — Design backwards
Analytical questions → star schema → staging → raw. The raw layer is designed
**last**, to supply the model above it. Designing raw first produces a layer
that cannot answer the questions the project exists to answer.

### Gate 3 — Security pre-flight
`.gitignore` in place and `.env` verified ignored **before** any credential is
typed. Connectivity proven using environment variables only.

See [SECURITY § Pre-flight checklist](SECURITY_AND_GOVERNANCE.md#9-pre-flight-checklist).

## Core principles

1. **A finished, explainable pipeline beats a sprawling, half-built one.**
   Depth of understanding over feature count.
2. **Classify every source before creating tables** — immutable event, mutable
   reference, or snapshot requiring history. This determines the load pattern
   and cannot be retrofitted cheaply.
3. **Every model declares and tests its grain.** This is the single
   highest-value test in the project.
4. **Design for failure from the start.** Retry, backoff, and a dead-letter
   record are built in from the first implementation, not added in a later
   hardening pass.
5. **The implementation must match the diagram.** If the architecture says the
   warehouse loads from the lake, the code loads from the lake.
6. **A description is a promise; a test is enforcement.** If a doc claims a
   rule, encode it as a test.
